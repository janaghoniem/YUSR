"""
Mini Prompt Generator - Creates minimal prompts for cache hits

When a template is found in cache, this generates a focused prompt
that asks LLM to only write the __main__ section with the new parameters.
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def extract_function_name(template_code: str) -> str:
    """Extract function name from template code"""
    import re
    match = re.search(r'def\s+(\w+)\s*\(', template_code)
    if match:
        return match.group(1)
    return "execute_action"


def extract_function_params(template_code: str) -> list:
    """Extract function parameter names from template"""
    import re
    match = re.search(r'def\s+\w+\s*\((.*?)\)', template_code, re.DOTALL)
    if match:
        params_str = match.group(1)
        params = [p.strip().split('=')[0].strip() for p in params_str.split(',') if p.strip()]
        return params
    return []


def generate_cache_hit_mini_prompt(template_code: str, user_query: str, input_data: str = None) -> str:
    """
    Generate a minimal prompt for LLM when template is found in cache.
    
    LLM only needs to write the __main__ section with proper parameter values.
    
    Args:
        template_code: The cached function template
        user_query: The original task query from user
        input_data: Optional input data for the task
    
    Returns:
        Minimal prompt for LLM
    """
    func_name = extract_function_name(template_code)
    params = extract_function_params(template_code)
    
    prompt = f"""
================================================================================
[CACHE HIT] - REUSING VALIDATED TEMPLATE
================================================================================

You have a validated function template that works. Your job is ONLY to write 
the __main__ section that calls this function with appropriate parameters.

TEMPLATE FUNCTION:
```python
{template_code}
```

USER'S TASK: {user_query}

YOUR TASK - Write ONLY the __main__ block:
1. Extract relevant parameters from the task description
2. Call {func_name}() with the extracted parameters
3. Check the return value and print appropriate status

IMPORTANT CONSTRAINTS:
- DO NOT modify the function definition
- DO NOT add extra logic beyond calling the function
- DO NOT import additional libraries (already imported in template)
- ONLY write the __main__ section with proper parameter values
- Make sure parameters match the function signature: {func_name}({', '.join(params)})

PARAMETER GUIDANCE:
"""
    
    if input_data:
        prompt += f"""
- Input data provided: {input_data[:100]}...
  (Use this for text_content, data, or similar parameters)
"""
    
    prompt += f"""

Write clean, minimal code. Example structure:

if __name__ == "__main__":
    success = {func_name}(param1_value, param2_value)
    
    if success:
        print("EXECUTION_SUCCESS")
    else:
        print("EXECUTION_FAILED")

GENERATE ONLY THE CODE BLOCK, nothing else.
"""
    
    logger.info(f"[MINI PROMPT] Function: {func_name}({', '.join(params)})")
    return prompt


if __name__ == "__main__":
    # Test mini prompt generation
    template = """def open_application(app_name):
    \"\"\"Open an application\"\"\"
    import os
    try:
        os.system(f'start {app_name}')
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False

if __name__ == "__main__":
    pass
"""
    
    prompt = generate_cache_hit_mini_prompt(
        template_code=template,
        user_query="Open Microsoft Excel",
        input_data=None
    )
    
    print(prompt)
