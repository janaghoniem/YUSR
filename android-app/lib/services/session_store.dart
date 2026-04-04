// lib/services/session_store.dart
import 'package:shared_preferences/shared_preferences.dart';

class SessionStore {
  static const _keyUserId    = 'aura_user_id';
  static const _keyUsername  = 'aura_username';
  static const _keySessionId = 'aura_session_id';
  static const _keyLanguage  = 'aura_language';
  static const _keyOnboarded = 'aura_onboarding_complete';

  static Future<void> save({
    required String userId,
    required String username,
    required String sessionId,
    required String language,
  }) async {
    final p = await SharedPreferences.getInstance();
    await p.setString(_keyUserId, userId);
    await p.setString(_keyUsername, username);
    await p.setString(_keySessionId, sessionId);
    await p.setString(_keyLanguage, language);
    await p.setBool(_keyOnboarded, true);
  }

  static Future<Map<String, String?>> load() async {
    final p = await SharedPreferences.getInstance();
    return {
      'userId':    p.getString(_keyUserId),
      'username':  p.getString(_keyUsername),
      'sessionId': p.getString(_keySessionId),
      'language':  p.getString(_keyLanguage),
    };
  }

  static Future<bool> isOnboarded() async {
    final p = await SharedPreferences.getInstance();
    return p.getBool(_keyOnboarded) ?? false;
  }

  static Future<void> clear() async {
    final p = await SharedPreferences.getInstance();
    await p.remove(_keyUserId);
    await p.remove(_keyUsername);
    await p.remove(_keySessionId);
    await p.remove(_keyLanguage);
    await p.remove(_keyOnboarded);
  }

  /// Update session ID only (called when user starts a new chat)
  static Future<void> updateSessionId(String sessionId) async {
    final p = await SharedPreferences.getInstance();
    await p.setString(_keySessionId, sessionId);
  }
}