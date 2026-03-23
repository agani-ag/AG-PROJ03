import { useState, useEffect } from 'react';
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

// ─── Point to Flask test server or Django backend ─────────────────────────────
const API_BASE =
  Platform.OS === 'android'
    ? 'http://10.0.2.2:5000'   // Android emulator → localhost
    : 'http://127.0.0.1:5000'; // iOS simulator / physical (change to machine IP)
// ─────────────────────────────────────────────────────────────────────────────

export default function LoginScreen({ onLoginSuccess }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [deviceId, setDeviceId] = useState('');
  const [toast, setToast] = useState({ visible: false, message: '', type: 'success' });

  useEffect(() => {
    getDeviceId().then(setDeviceId);
  }, []);

  const showToast = (message, type = 'success') => {
    setToast({ visible: true, message, type });
    setTimeout(() => setToast((t) => ({ ...t, visible: false })), 3200);
  };

  const handleLogin = async () => {
    if (!email || !password) {
      showToast('Please enter email and password.', 'error');
      return;
    }

    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/api/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, device_id: deviceId }),
      });

      const data = await response.json();

      if (response.ok && data.success) {
        showToast(`Welcome back, ${data.username}!`, 'success');
        setTimeout(() => {
          onLoginSuccess({
            username: data.username,
            reversedPassword: data.reversed_password,
            deviceId: data.device_id,
            urls: data.urls || {},
          });
        }, 1200);
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

      <View style={styles.header}>
        <Text style={styles.appName}>MS</Text>
        <Text style={styles.tagline}>Welcome back</Text>
      </View>

      <View style={styles.form}>
        <View style={styles.inputGroup}>
          <Text style={styles.label}>Email</Text>
          <TextInput
            style={styles.input}
            placeholder="Enter your email"
            placeholderTextColor="#aaa"
            value={email}
            onChangeText={setEmail}
            keyboardType="email-address"
            autoCapitalize="none"
            autoCorrect={false}
          />
        </View>

        <View style={styles.inputGroup}>
          <Text style={styles.label}>Password</Text>
          <View style={styles.passwordWrapper}>
            <TextInput
              style={[styles.input, styles.passwordInput]}
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

      {deviceId ? (
        <Text style={styles.deviceIdText}>Device: {deviceId.substring(0, 16)}…</Text>
      ) : null}
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
  passwordWrapper: { flexDirection: 'row', alignItems: 'center' },
  passwordInput: {
    flex: 1,
    borderTopRightRadius: 0,
    borderBottomRightRadius: 0,
    borderRightWidth: 0,
  },
  toggleBtn: {
    borderWidth: 1,
    borderColor: '#e0e0e0',
    borderLeftWidth: 0,
    borderTopRightRadius: 10,
    borderBottomRightRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    backgroundColor: '#fafafa',
  },
  toggleText: { fontSize: 13, color: '#4a90e2', fontWeight: '600' },
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
