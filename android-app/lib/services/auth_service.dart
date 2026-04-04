// lib/services/auth_service.dart
import 'dart:convert';
import 'package:http/http.dart' as http;

class AuthService {
  static const String backendUrl = 'http://10.0.2.2:8000';

  /// Generate a client-side user ID (used only at signup before server assigns one)
  static String generateUserId() {
    final ts = DateTime.now().millisecondsSinceEpoch;
    final rand = (ts % 999999).toString().padLeft(6, '0');
    return 'user_${ts}_$rand';
  }

  /// Check if username is available
  static Future<bool> checkUsernameAvailable(String username) async {
    try {
      final r = await http.get(
        Uri.parse(
            '$backendUrl/onboarding/check-username?username=${Uri.encodeComponent(username)}'),
      );
      if (r.statusCode == 200) {
        final data = jsonDecode(r.body) as Map<String, dynamic>;
        return data['available'] == true;
      }
    } catch (_) {}
    return false;
  }


  /// Register face biometrics — base64 jpeg string from camera
  static Future<Map<String, dynamic>> registerFace({
    required String userId,
    required String username,
    required String faceImageBase64,
  }) async {
    final r = await http.post(
      Uri.parse('$backendUrl/onboarding/register-face'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'user_id': userId,
        'username': username,
        'face_image': faceImageBase64,
      }),
    );
    if (r.statusCode != 200) {
      final err = jsonDecode(r.body) as Map<String, dynamic>;
      throw Exception(err['detail'] ?? 'Face registration failed (HTTP ${r.statusCode})');
    }
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  /// Create account after face is registered
  static Future<Map<String, dynamic>> createAccount({
    required String userId,
    required String username,
    required String introduction,
    required Map<String, dynamic> preferences,
  }) async {
    final r = await http.post(
      Uri.parse('$backendUrl/onboarding/create-account'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'user_id': userId,
        'username': username,
        'password': '',          // face-only; empty string is accepted by backend
        'introduction': introduction,
        'preferences': preferences,
      }),
    );
    if (r.statusCode != 200) {
      final err = jsonDecode(r.body);
      throw Exception(err['detail'] ?? 'Account creation failed');
    }
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  /// Store onboarding Q&A answers as Mem0 preferences
  static Future<void> storeIntroduction({
    required String userId,
    required String language,
    required Map<String, String> answers,
  }) async {
    try {
      await http.post(
        Uri.parse('$backendUrl/onboarding/store-introduction'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'user_id': userId,
          'language': language,
          'answers': answers,
        }),
      );
    } catch (_) {
      // Non-fatal — memory can be rebuilt from conversations
    }
  }

  /// Face-only login — no username needed
  static Future<Map<String, dynamic>> loginFaceOnly(
      String faceImageBase64) async {
    final r = await http.post(
      Uri.parse('$backendUrl/onboarding/login-face-only'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'face_image': faceImageBase64}),
    );
    if (r.statusCode != 200) {
      final err = jsonDecode(r.body);
      throw Exception(err['detail'] ?? 'Face not recognized');
    }
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  /// Create a server-side session ID tied to user_id
  static Future<String> createSession(String userId) async {
    try {
      final r = await http.post(
        Uri.parse('$backendUrl/onboarding/session/create'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'user_id': userId}),
      );
      if (r.statusCode == 200) {
        final data = jsonDecode(r.body) as Map<String, dynamic>;
        return data['session_id'] as String;
      }
    } catch (_) {}
    // Fallback: client-side generation if server unreachable
    final ts = DateTime.now().millisecondsSinceEpoch;
    return 'session_${userId}_$ts';
  }
}