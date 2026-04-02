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
  ScrollView,
  FlatList,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import Toast from '../components/Toast';
import { getDeviceId } from '../utils/deviceId';
import { useAppName } from '../utils/AppContext';
import { useApiConfig } from '../utils/ApiConfig';
import { registerForPushNotifications, registerTokenWithBackend } from '../utils/notifications';
import { saveCredentials } from '../utils/secureAuth';

import { collectDeviceMetadata, sendAuditLog } from '../utils/auditLogger';
import AppBrand from '../components/AppBrand';
import DeveloperSettings from '../components/DeveloperSettings';
import PinEntry from '../components/PinEntry';

export default function LoginScreen({ onLoginSuccess }) {
  const { updateAppName } = useAppName();
  const { currentUrl, checkHealth, updateFallback, isUsingFallback, healthCheckData } = useApiConfig();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [deviceId, setDeviceId] = useState('');
  const [toast, setToast] = useState({ visible: false, message: '', type: 'success' });
  const [showPinEntry, setShowPinEntry] = useState(false);
  const [showDeveloperSettings, setShowDeveloperSettings] = useState(false);
  const [loginMode, setLoginMode] = useState(''); // Instance selected from health check
  const [availableInstances, setAvailableInstances] = useState([]); // From health check
  const [showInstanceDropdown, setShowInstanceDropdown] = useState(false);

  const clickCountRef = useRef(0);
  const clickTimerRef = useRef(null);

  // Health check on mount
  useEffect(() => {
    initializeScreen();
  }, []);

  // Update instances when health check data changes
  useEffect(() => {
    if (healthCheckData && Array.isArray(healthCheckData.instance)) {
      setAvailableInstances(healthCheckData.instance);
      console.log('[Login] Available instances:', healthCheckData.instance);
      
      // Auto-select if only one instance
      if (healthCheckData.instance.length === 1) {
        setLoginMode(healthCheckData.instance[0]);
        console.log('[Login] Auto-selected single instance:', healthCheckData.instance[0]);
      } else if (healthCheckData.instance.length > 1) {
        // Load stored preference if available
        loadStoredLoginMode();
      }
    }
  }, [healthCheckData]);

  const initializeScreen = async () => {
    try {
      await checkHealth();
      getDeviceId().then(setDeviceId);
    } catch (err) {
      console.error('[Login] Initialization error:', err);
    }
  };

  const loadStoredLoginMode = async () => {
    try {
      const { getStoredCredentials } = await import('../utils/secureAuth');
      const creds = await getStoredCredentials();
      if (creds && creds.loginMode && availableInstances.includes(creds.loginMode)) {
        setLoginMode(creds.loginMode);
        console.log('[Login] Restored instance preference:', creds.loginMode);
      } else if (availableInstances.length > 0) {
        // If stored preference not available or not in current instances, use first available
        setLoginMode(availableInstances[0]);
      }
    } catch (err) {
      console.error('[Login] Error loading stored login mode:', err);
    }
  };

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

    // Show PIN entry on 10th click
    if (clickCountRef.current >= 10) {
      clickCountRef.current = 0;
      clearTimeout(clickTimerRef.current);
      setShowPinEntry(true);
      showToast('Enter PIN to access Developer Settings', 'success');
    }
  };

  const handlePinSuccess = () => {
    setShowPinEntry(false);
    setShowDeveloperSettings(true);
    showToast('Developer Settings unlocked!', 'success');
  };

  const handleLogin = async () => {
    if (!email || !password) {
      showToast('Please enter email/username and password.', 'error');
      return;
    }

    if (availableInstances.length > 0 && !loginMode) {
      showToast('Please select an instance.', 'error');
      return;
    }

    setLoading(true);
    try {
      // Single API endpoint with instance in body
      const apiEndpoint = `${currentUrl}/device/api/login`;

      const requestBody = {
        email,
        password,
        device_id: deviceId,
      };

      // Include instance if available
      if (loginMode) {
        requestBody.instance = loginMode;
      }

      const response = await fetch(apiEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
      });

      const data = await response.json();

      if (response.ok && data.success) {

        await updateAppName(data.business_name?.trim() || 'SyncUp');
        showToast(`Welcome back, ${data.username}!`, 'success');

        // Save credentials securely for auto-login (including selected instance)
        await saveCredentials(email, password, loginMode);
        console.log('[Login] Credentials and instance preference saved for auto-login');

        // Collect and send audit log with comprehensive device metadata
        setTimeout(async () => {
          try {
            console.log('[Login] Collecting device metadata for audit log...');
            const metadata = await collectDeviceMetadata();
            await sendAuditLog(currentUrl, email, deviceId, 'login', metadata);
          } catch (err) {
            console.error('[Login] Audit log error:', err);
          }
        }, 500);

        // Register for Firebase push notifications
        try {
          const { token, error } = await registerForPushNotifications();

          if (token) {
            const success = await registerTokenWithBackend(currentUrl, deviceId, email, token, loginMode);
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
            loginId: email, // Store the email/username used for login (for logout)
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

      <PinEntry
        visible={showPinEntry}
        onClose={() => setShowPinEntry(false)}
        onSuccess={handlePinSuccess}
      />

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

        {/* Instance Selector Dropdown - Only show if multiple instances */}
        {availableInstances.length > 1 && (
          <View style={styles.instanceSelectorContainer}>
            <Text style={styles.label}>Instance</Text>
            <TouchableOpacity
              style={styles.instanceSelectorButton}
              onPress={() => setShowInstanceDropdown(!showInstanceDropdown)}
              activeOpacity={0.7}
            >
              <Text style={styles.instanceSelectorText}>
                {loginMode || 'Select Instance'}
              </Text>
              <Text style={styles.instanceSelectorArrow}>
                {showInstanceDropdown ? '▲' : '▼'}
              </Text>
            </TouchableOpacity>

            {showInstanceDropdown && (
              <View style={styles.instanceDropdownList}>
                {availableInstances.map((instance) => (
                  <TouchableOpacity
                    key={instance}
                    style={[
                      styles.instanceDropdownItem,
                      loginMode === instance && styles.instanceDropdownItemActive,
                    ]}
                    onPress={() => {
                      setLoginMode(instance);
                      setShowInstanceDropdown(false);
                      console.log('[Login] Instance selected:', instance);
                    }}
                    activeOpacity={0.7}
                  >
                    <Text
                      style={[
                        styles.instanceDropdownItemText,
                        loginMode === instance && styles.instanceDropdownItemTextActive,
                      ]}
                    >
                      {instance}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>
            )}
          </View>
        )}

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
  
  // Instance Selector Dropdown
  instanceSelectorContainer: {
    marginBottom: 18,
  },
  instanceSelectorButton: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#e0e0e0',
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    backgroundColor: '#fafafa',
  },
  instanceSelectorText: {
    fontSize: 15,
    color: '#1a1a2e',
    fontWeight: '500',
    flex: 1,
  },
  instanceSelectorArrow: {
    fontSize: 12,
    color: '#999',
    fontWeight: '700',
  },
  instanceDropdownList: {
    position: 'absolute',
    top: 88,
    left: 0,
    right: 0,
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: '#4a90e2',
    borderRadius: 10,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.12,
    shadowRadius: 8,
    elevation: 6,
    zIndex: 1000,
    overflow: 'hidden',
  },
  instanceDropdownItem: {
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#f0f0f0',
  },
  instanceDropdownItemActive: {
    backgroundColor: '#f0f4f8',
  },
  instanceDropdownItemText: {
    fontSize: 14,
    color: '#666',
    fontWeight: '500',
  },
  instanceDropdownItemTextActive: {
    color: '#4a90e2',
    fontWeight: '700',
  },  loginBtn: {
    backgroundColor: '#1a1a2e',
    borderRadius: 12,
    paddingVertical: 15,
    alignItems: 'center',
  },
  loginBtnDisabled: { opacity: 0.6 },
  loginBtnText: { color: '#fff', fontSize: 16, fontWeight: '700', letterSpacing: 1 },
  deviceIdText: { textAlign: 'center', marginTop: 16, fontSize: 11, color: '#bbb' },
});
