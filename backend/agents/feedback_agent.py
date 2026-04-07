import os
import json
import logging
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
import uuid

try:
    from groq import Groq
except ImportError:
    Groq = None
from dotenv import load_dotenv

# Assuming TaskMemory is imported here if you wanted to pass it in directly
from agents.execution_agent.strategies.task_memory import TaskMemory

logger = logging.getLogger(__name__)
load_dotenv()

class EvaluationResult(BaseModel):
    score: float = Field(description="Score from 0.0 (total failure) to 1.0 (perfect success)")
    is_success: bool = Field(description="True if the task achieved the user's core goal")
    reasoning: str = Field(description="Detailed explanation of why this score was given")
    improvements: List[str] = Field(description="Actionable steps the agent should take next time to avoid failures")

class FeedbackAgent:
    '''Critic Agent that evaluates execution trajectories for a given task,
    scoring their success and generating actionable feedback to be stored in TaskMemory.'''
    def __init__(self, model_name: str = 'llama-3.3-70b-versatile'):
        self.api_key = os.environ.get('GROQ_API_KEY')
        if not self.api_key:
            logger.warning('FeedbackAgent: GROQ_API_KEY missing from environment.')
            
        self.client = Groq(api_key=self.api_key) if self.api_key and Groq else None
        self.model_name = model_name
        self.memory = None # Can be injected or initialized later

    def attach_memory(self, memory: 'TaskMemory'):
        '''Attach a TaskMemory instance to seamlessly store evaluations.'''
        self.memory = memory

    def evaluate_and_store(self, goal: str, trajectory: List[Dict[str, Any]], user_feedback: Optional[str] = None) -> EvaluationResult:
        '''Evaluates trajectory and stores feedback immediately in TaskMemory.'''
        result = self.evaluate_execution(goal, trajectory, user_feedback)
        
        # We only really want to store reflections if there are actionable improvements
        if self.memory and (result.is_success or result.improvements):
            logger.info(f"FeedbackAgent: Storing evaluation for {goal[:50]} (Score: {result.score})")
            self.memory.store_trajectory_feedback(
                goal=goal,
                evaluation_score=result.score,
                is_success=result.is_success,
                improvements=result.improvements
            )
            
        return result

    def evaluate_execution(self, goal: str, trajectory: List[Dict[str, Any]], user_feedback: Optional[str] = None) -> EvaluationResult:
        '''Evaluates a sequence of actions taken by the agents.
        Returns a structured EvaluationResult indicating success, a continuous score,
        and specific improvements.'''
        if not self.client:
            logger.error('FeedbackAgent: Cannot evaluate - Groq client unavailable.')
            return EvaluationResult(score=0.0, is_success=False, reasoning='Groq client not initialized.', improvements=[])

        prompt = self._build_evaluation_prompt(goal, trajectory, user_feedback)
        
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        'role': 'system', 
                        'content': 'You are an strict, objective AI Critic evaluating an automated agent system. Evaluate the execution trajectory based on the users goal. Return ONLY valid JSON matching the requested schema.'
                    },
                    {'role': 'user', 'content': prompt}
                ],
                response_format={'type': 'json_object'},
                max_tokens=1000,
                temperature=0.1
            )
            
            content = response.choices[0].message.content
            data = json.loads(content)
            
            return EvaluationResult(
                score=float(data.get('score', 0.0)),
                is_success=bool(data.get('is_success', False)),
                reasoning=str(data.get('reasoning', 'No reasoning provided')),
                improvements=list(data.get('improvements', []))
            )
            
        except Exception as e:
            logger.error(f'FeedbackAgent evaluation failed: {e}')
            return EvaluationResult(score=0.0, is_success=False, reasoning=f'Evaluation pipeline crashed: {str(e)}', improvements=[])

    def _build_evaluation_prompt(self, goal: str, trajectory: List[Dict[str, Any]], user_feedback: Optional[str]) -> str:
        safe_trajectory = json.dumps(trajectory, indent=2)[0:5000]
        
        return f'''
Evaluate the following automated task execution:

---
GOAL: {goal}

USER FEEDBACK / CORRECTIONS: {user_feedback or "None provided"}

EXECUTION TRAJECTORY (Actions taken and results):
{safe_trajectory}
---

Based on the evidence, determine:
1. Did the system successfully achieve the final state the user requested?
2. Were there redundant, failing, or inefficient steps?
3. How should the agent behave next time it receives a similar task?

Respond strictly in this JSON format:
{{
    "score": 0.0,
    "is_success": true,
    "reasoning": "Detailed step-by-step reasoning",
    "improvements": [
        "Use a CSS selector Instead of XPath for dynamic buttons",
        "Wait for page load before typing"
    ] 
}}
'''
