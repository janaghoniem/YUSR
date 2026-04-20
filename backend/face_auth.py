# face_auth.py - Updated with stricter thresholds
import base64
import numpy as np
from datetime import datetime
import cv2
import face_recognition
from pymongo import MongoClient
import os
import logging
import hashlib
import hmac
import json

logger = logging.getLogger(__name__)

class FaceAuth:
    def __init__(self):
        # Initialize MongoDB connection
        self.client = MongoClient(os.getenv("MONGODB_URI"))
        self.db = self.client["aura_db"]
        self.face_data_col = self.db["face_auth_data"]
        
        # Secret key for additional encryption (store in .env)
        self.secret_key = os.getenv("FACE_ENCRYPTION_KEY", "your-secret-key-change-this")
        
        # STRICT THRESHOLDS for high security
        # In face_recognition library:
        # - 0.3 and below = almost identical (twins/same person in same conditions)
        # - 0.3-0.35 = excellent match
        # - 0.35-0.4 = good match  
        # - 0.4-0.45 = fair match
        # - >0.45 = likely different person
        self.max_acceptable_distance = 0.45  # Maximum allowed distance (stricter than 0.6)
        self.min_confidence_percent = 70.0   # Minimum 70% confidence required
        
    def process_face_image(self, image_base64):
        """
        Process base64 image and extract face encoding
        Returns face encoding as list of floats
        """
        try:
            # Remove data URL prefix if present
            if ',' in image_base64:
                image_base64 = image_base64.split(',')[1]
            
            # Decode base64 to image
            image_data = base64.b64decode(image_base64)
            nparr = np.frombuffer(image_data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if img is None:
                return None, "Failed to decode image"
            
            # Convert BGR to RGB (face_recognition uses RGB)
            rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            
            # Detect face locations with higher accuracy model
            # Use CNN model if available (better accuracy), fallback to HOG
            # try:
            #     face_locations = face_recognition.face_locations(rgb_img, model="cnn")
            # except:
            #     face_locations = face_recognition.face_locations(rgb_img, model="hog")
            # Use HOG model — reliable on CPU servers without CUDA dlib
            # CNN requires dlib compiled with CUDA; HOG works everywhere
            face_locations = face_recognition.face_locations(rgb_img, model="hog")
            if not face_locations:
                return None, "No face detected in the image"
            
            if len(face_locations) > 1:
                return None, "Multiple faces detected. Please ensure only your face is visible."
            
            # Get face encoding with multiple jitters for better accuracy
            # Higher jitters = more accurate but slower
            face_encodings = face_recognition.face_encodings(rgb_img, face_locations, num_jitters=5)
            
            if not face_encodings:
                return None, "Could not extract face features"
            
            # Convert numpy array to list for JSON serialization
            encoding = face_encodings[0].tolist()
            
            return encoding, "Success"
            
        except Exception as e:
            logger.error(f"Face processing error: {e}")
            return None, f"Error processing image: {str(e)}"
    
    def calculate_confidence(self, distance):
        """
        Convert face distance to confidence percentage
        Stricter scaling for better discrimination
        """
        # Perfect match = distance 0 -> 100% confidence
        # Distance 0.3 -> 90% confidence
        # Distance 0.35 -> 80% confidence
        # Distance 0.4 -> 70% confidence
        # Distance 0.45 -> 60% confidence
        # Distance > 0.45 -> rapidly declining
        
        if distance <= 0.3:
            # Excellent match: 100% to 90%
            confidence = 100 - (distance / 0.3) * 10
        elif distance <= 0.35:
            # Very good match: 90% to 80%
            confidence = 90 - ((distance - 0.3) / 0.05) * 10
        elif distance <= 0.4:
            # Good match: 80% to 70%
            confidence = 80 - ((distance - 0.35) / 0.05) * 10
        elif distance <= 0.45:
            # Fair match: 70% to 60%
            confidence = 70 - ((distance - 0.4) / 0.05) * 10
        else:
            # Poor match: less than 60%
            confidence = max(0, 60 - ((distance - 0.45) / 0.05) * 30)
        
        return round(confidence, 1)
    
    def _encrypt_encoding(self, encoding):
        """Add an extra layer of encryption to the face encoding"""
        encoding_str = json.dumps(encoding)
        # Create HMAC signature for integrity
        signature = hmac.new(
            self.secret_key.encode(),
            encoding_str.encode(),
            hashlib.sha256
        ).hexdigest()
        
        return {
            "encoding": encoding,
            "signature": signature,
            "version": "1.0"
        }
    
    def _verify_encoding(self, stored_data):
        """Verify the integrity of stored face encoding"""
        if not stored_data:
            return None
        
        encoding = stored_data.get("encoding")
        stored_signature = stored_data.get("signature")
        
        if not encoding or not stored_signature:
            return encoding  # Legacy format, still usable but not verified
        
        # Verify signature
        encoding_str = json.dumps(encoding)
        expected_signature = hmac.new(
            self.secret_key.encode(),
            encoding_str.encode(),
            hashlib.sha256
        ).hexdigest()
        
        if expected_signature != stored_signature:
            logger.warning("Face encoding signature mismatch - possible corruption")
            return None
        
        return encoding
    
    # def store_face_data(self, user_id, username, face_encoding):
    #     """Store face encoding securely in MongoDB"""
    #     try:
    #         # Encrypt the encoding before storage
    #         encrypted_data = self._encrypt_encoding(face_encoding)
            
    #         face_doc = {
    #             "user_id": user_id,
    #             "username": username,
    #             "face_encoding_data": encrypted_data,
    #             "created_at": datetime.utcnow().isoformat(),
    #             "updated_at": datetime.utcnow().isoformat(),
    #             "auth_type": "face"
    #         }
            
    #         # Upsert (update if exists, insert if not)
    #         result = self.face_data_col.update_one(
    #             {"user_id": user_id},
    #             {"$set": face_doc},
    #             upsert=True
    #         )
            
    #         logger.info(f"Face data stored for user: {username}")
    #         return True, "Face biometrics registered successfully"
            
    #     except Exception as e:
    #         logger.error(f"Failed to store face data: {e}")
    #         return False, f"Failed to store face data: {str(e)}"
    
    def store_face_data(self, user_id, username, face_encoding):
        """Store face encoding securely in MongoDB"""
        try:
            # Encrypt the encoding before storage
            encrypted_data = self._encrypt_encoding(face_encoding)
            
            face_doc = {
                "user_id": user_id,
                "username": username,
                "face_encoding_data": encrypted_data,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
                "auth_type": "face"
            }
            
            logger.info(f"💾 Storing face data for user_id={user_id}, username={username}")
            
            # Upsert (update if exists, insert if not)
            result = self.face_data_col.update_one(
                {"user_id": user_id},
                {"$set": face_doc},
                upsert=True
            )
            
            logger.info(f"✅ Face data stored — matched={result.matched_count}, modified={result.modified_count}, upserted_id={result.upserted_id}")
            
            # Immediate read-back verification
            saved = self.face_data_col.find_one({"user_id": user_id})
            if saved:
                logger.info(f"✅ Verified in DB: username={saved.get('username')}, has_encoding={bool(saved.get('face_encoding_data'))}")
            else:
                logger.error(f"❌ Read-back FAILED — document not found after upsert for user_id={user_id}")
                return False, "Face data failed to persist after write"
            
            return True, "Face biometrics registered successfully"
            
        except Exception as e:
            logger.error(f"❌ Failed to store face data: {e}", exc_info=True)
            return False, f"Failed to store face data: {str(e)}"


    def verify_face(self, username, current_face_encoding):
        """
        Compare current face encoding with stored face encoding
        Uses stricter thresholds for better security
        """
        try:
            # Find user by username
            user_data = self.face_data_col.find_one({"username": username})
            
            if not user_data:
                return False, "User not found. Please sign up first."
            
            # Retrieve and verify stored encoding
            stored_encrypted = user_data.get("face_encoding_data")
            if not stored_encrypted:
                return False, "Face data not found for this user"
            
            stored_encoding = self._verify_encoding(stored_encrypted)
            if stored_encoding is None:
                return False, "Face data corrupted. Please re-register."
            
            # Convert to numpy arrays for distance calculation
            stored_array = np.array(stored_encoding)
            current_array = np.array(current_face_encoding)
            
            # Calculate Euclidean distance between face encodings
            distance = np.linalg.norm(stored_array - current_array)
            
            # Calculate confidence percentage
            confidence = self.calculate_confidence(distance)
            
            logger.info(f"Face verification for {username}: distance={distance:.4f}, confidence={confidence:.1f}%")
            
            # Stricter acceptance criteria
            if distance <= self.max_acceptable_distance and confidence >= self.min_confidence_percent:
                logger.info(f"✅ Face match successful for {username} (confidence: {confidence:.1f}%)")
                return True, {
                    "user_id": user_data["user_id"],
                    "username": user_data["username"],
                    "confidence": confidence / 100,
                    "distance": round(distance, 4),
                    "confidence_percent": confidence
                }
            else:
                logger.warning(f"❌ Face match failed for {username} (distance: {distance:.4f}, confidence: {confidence:.1f}%, required: {self.min_confidence_percent:.0f}%)")
                return False, f"Face not recognized (confidence: {confidence:.1f}%, need > {self.min_confidence_percent:.0f}%)"
                
        except Exception as e:
            logger.error(f"Face verification error: {e}")
            return False, f"Verification failed: {str(e)}"
    
    def get_user_face_status(self, username):
        """Check if a user has face biometrics registered"""
        try:
            user_data = self.face_data_col.find_one({"username": username})
            return user_data is not None
        except Exception as e:
            logger.error(f"Error checking face status: {e}")
            return False
    
    def delete_face_data(self, user_id):
        """Delete face data for a user"""
        try:
            result = self.face_data_col.delete_one({"user_id": user_id})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting face data: {e}")
            return False

# Create singleton instance
face_auth = FaceAuth()