import { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import Toast from '../components/Toast';
import { getDeviceId } from '../utils/deviceId';
import { useAppName } from '../utils/AppContext';
import { useApiConfig } from '../utils/ApiConfig';
import { registerForPushNotifications, registerTokenWithBackend } from '../utils/notifications';
import AppBrand from '../components/AppBrand';
import DeveloperSettings from '../components/DeveloperSettings';

export default function LoginScreen({ onLoginSuccess }) {
  const { updateAppName } = useAppName();
  const { currentUrl, checkHealth, updateFallback, isUsingFallback } = useApiConfig();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [deviceId, setDeviceId] = useState('');
  const [toast, setToast] = useState({ visible: false, message: '', type: 'success' });
  const [showDeveloperSettings, setShowDeveloperSettings] = useState(false);

  const clickCountRef = useRef(0);
  const clickTimerRef = useRef(null);

  // Health check on mount
  useEffect(() => {
    checkHealth();
    getDeviceId().then(setDeviceId);
  }, []);

  const showToast = (message, type = 'success') => {
    setToast({ visible: true, message, type });
    setTimeout(() => setToast((t) => ({ ...t, visible: false })), 3200);
  };

  // 10-click counter for developer settings
  const handleAppNamePress = () => {
    clickCountRef.current += 1;

    // Clear timer
    if (clickTimerRef.current) {
      clearTimeout(clickTimerRef.current);
    }

    // Reset counter after 3 seconds of inactivity
    clickTimerRef.current = setTimeout(() => {
      clickCountRef.current = 0;
    }, 3000);

    // Open developer settings on 10th click
    if (clickCountRef.current >= 10) {
      clickCountRef.current = 0;
      clearTimeout(clickTimerRef.current);
      setShowDeveloperSettings(true);
      showToast('Developer Settings unlocked!', 'success');
    }
  };

  const handleLogin = async () => {
    if (!email || !password) {
      showToast('Please enter email/username and password.', 'error');
      return;
    }

    setLoading(true);
    try {
      const response = await fetch(`${currentUrl}/api/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, device_id: deviceId }),
      });

      const data = await response.json();

      if (response.ok && data.success) {
        // Save fallback URL from response
        if (data.fallback_url) {
          await updateFallback(data.fallback_url);
        }

        await updateAppName(data.business_name?.trim() || 'MS');
        showToast(`Welcome back, ${data.username}!`, 'success');

        // Register for Firebase push notifications
        try {
          const { token, error } = await registerForPushNotifications();

          if (token) {
            const success = await registerTokenWithBackend(currentUrl, deviceId, email, token);
            if (success) {
              showToast('Notifications enabled!', 'success');
            } else {
              showToast('Notification registration failed', 'error');
            }
          } else if (error) {
            showToast(`Notification error: ${error}`, 'error');
          }
        } catch (err) {
          console.error('[Login] Notification error:', err.message);
          showToast(`Notification error: ${err.message}`, 'error');
        }

        // Navigate after notification setup
        setTimeout(() => {
          onLoginSuccess({
            username: data.username,
            reversedPassword: data.reversed_password,
            deviceId: data.device_id,
            urls: data.urls || {},
          });
        }, 2000);
      } else {
        showToast(data.message || 'Invalid credentials.', 'error');
      }
    } catch (error) {
      showToast('Cannot connect to server. Check your network.', 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
    >
      <StatusBar style="dark" />

      <Toast visible={toast.visible} message={toast.message} type={toast.type} />

      <DeveloperSettings
        visible={showDeveloperSettings}
        onClose={() => setShowDeveloperSettings(false)}
      />

      <View style={styles.header}>
        <TouchableOpacity onPress={handleAppNamePress} activeOpacity={1}>
          <AppBrand textStyle={styles.appName} />
        </TouchableOpacity>
        <Text style={styles.tagline}>Welcome back</Text>
        {isUsingFallback && (
          <Text style={styles.fallbackIndicator}>Using Fallback Server</Text>
        )}
      </View>

      <View style={styles.form}>
        <View style={styles.inputGroup}>
          <Text style={styles.label}>Username / Email</Text>
          <TextInput
            style={styles.input}
            placeholder="Enter your username or email"
            placeholderTextColor="#aaa"
            value={email}
            onChangeText={setEmail}
            autoCapitalize="none"
            autoCorrect={false}
          />
        </View>

        <View style={styles.inputGroup}>
          <Text style={styles.label}>Password</Text>
          <View style={styles.passwordWrapper}>
            <TextInput
              style={styles.passwordInput}
              placeholder="Enter your password"
              placeholderTextColor="#aaa"
              value={password}
              onChangeText={setPassword}
              secureTextEntry={!showPassword}
              autoCapitalize="none"
            />
            <TouchableOpacity
              style={styles.toggleBtn}
              onPress={() => setShowPassword(!showPassword)}
              activeOpacity={0.6}
            >
              <Text style={styles.toggleText}>{showPassword ? 'Hide' : 'Show'}</Text>
            </TouchableOpacity>
          </View>
        </View>

        <TouchableOpacity style={styles.forgotBtn}>
          <Text style={styles.forgotText}>Forgot Password?</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={[styles.loginBtn, loading && styles.loginBtnDisabled]}
          onPress={handleLogin}
          disabled={loading}
        >
          {loading ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.loginBtnText}>Login</Text>
          )}
        </TouchableOpacity>
      </View>

      {/* {deviceId ? (
        <Text style={styles.deviceIdText}>Device: {deviceId.substring(0, 16)}…</Text>
      ) : null} */}
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#f5f7fa',
    justifyContent: 'center',
    paddingHorizontal: 28,
  },
  header: { alignItems: 'center', marginBottom: 40 },
  appName: { fontSize: 52, fontWeight: '800', color: '#1a1a2e', letterSpacing: 2 },
  tagline: { fontSize: 16, color: '#666', marginTop: 6 },
  fallbackIndicator: {
    fontSize: 11,
    color: '#ff9800',
    fontWeight: '700',
    marginTop: 4,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  form: {
    backgroundColor: '#fff',
    borderRadius: 20,
    padding: 24,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.08,
    shadowRadius: 12,
    elevation: 5,
  },
  inputGroup: { marginBottom: 18 },
  label: { fontSize: 13, fontWeight: '600', color: '#444', marginBottom: 6 },
  input: {
    borderWidth: 1,
    borderColor: '#e0e0e0',
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 15,
    color: '#1a1a2e',
    backgroundColor: '#fafafa',
  },
  passwordWrapper: {
    position: 'relative',
  },
  passwordInput: {
    borderWidth: 1,
    borderColor: '#e0e0e0',
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    paddingRight: 60,
    fontSize: 15,
    color: '#1a1a2e',
    backgroundColor: '#fafafa',
  },
  toggleBtn: {
    position: 'absolute',
    right: 4,
    top: 4,
    bottom: 4,
    paddingHorizontal: 12,
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: 8,
    backgroundColor: 'transparent',
  },
  toggleText: { fontSize: 13, color: '#4a90e2', fontWeight: '700' },
  forgotBtn: { alignSelf: 'flex-end', marginBottom: 24 },
  forgotText: { fontSize: 13, color: '#4a90e2', fontWeight: '500' },
  loginBtn: {
    backgroundColor: '#1a1a2e',
    borderRadius: 12,
    paddingVertical: 15,
    alignItems: 'center',
  },
  loginBtnDisabled: { opacity: 0.6 },
  loginBtnText: { color: '#fff', fontSize: 16, fontWeight: '700', letterSpacing: 1 },
  deviceIdText: { textAlign: 'center', marginTop: 16, fontSize: 11, color: '#bbb' },
});
