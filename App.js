import { useState, useCallback, useEffect } from 'react';
import { Alert, View, ActivityIndicator, StyleSheet, Text } from 'react-native';
import { AppProvider } from './utils/AppContext';
import { ApiConfigProvider, useApiConfig } from './utils/ApiConfig';
import { unregisterDevice, clearUserData } from './utils/logout';
import { hasStoredCredentials, getStoredCredentials, authenticateWithDevice } from './utils/secureAuth';
import { getDeviceId } from './utils/deviceId';
import { registerForPushNotifications, registerTokenWithBackend } from './utils/notifications';
import LoginScreen from './screens/LoginScreen';
import URLSelectorScreen from './screens/URLSelectorScreen';
import HomeScreen from './screens/HomeScreen';

function RootNavigator() {
  const { currentUrl } = useApiConfig();
  const [user, setUser] = useState(null);
  const [webUrl, setWebUrl] = useState(null);
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);

  // Check for saved credentials on app start
  useEffect(() => {
    checkSavedCredentials();
  }, []);

  const checkSavedCredentials = async () => {
    try {
      const hasCreds = await hasStoredCredentials();

      if (!hasCreds) {
        // No saved credentials - show login screen
        setIsCheckingAuth(false);
        return;
      }

      console.log('[AutoLogin] Saved credentials found');

      // Authenticate with device security
      const authResult = await authenticateWithDevice();

      if (!authResult.success) {
        // Authentication failed - show login screen
        console.log('[AutoLogin] Device authentication failed');
        setIsCheckingAuth(false);
        return;
      }

      if (authResult.noSecurity) {
        console.log('[AutoLogin] No device security, proceeding with auto-login');
      } else {
        console.log('[AutoLogin] Device authentication successful');
      }

      // Get stored credentials
      const creds = await getStoredCredentials();
      if (!creds) {
        setIsCheckingAuth(false);
        return;
      }

      // Auto-login with stored credentials
      console.log('[AutoLogin] Attempting auto-login...');
      await performAutoLogin(creds.email, creds.password);

    } catch (err) {
      console.error('[AutoLogin] Error:', err);
      setIsCheckingAuth(false);
    }
  };

  const performAutoLogin = async (email, password) => {
    try {
      const deviceId = await getDeviceId();

      const response = await fetch(`${currentUrl}/api/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, device_id: deviceId }),
      });

      const data = await response.json();

      if (response.ok && data.success) {
        console.log('[AutoLogin] Login successful');

        // Register for push notifications
        try {
          const { token } = await registerForPushNotifications();
          if (token) {
            await registerTokenWithBackend(currentUrl, deviceId, email, token);
          }
        } catch (err) {
          console.warn('[AutoLogin] Push notification registration failed:', err);
        }

        // Set user data
        setUser({
          username: data.username,
          reversedPassword: data.reversed_password,
          deviceId: data.device_id,
          loginId: email,
          urls: data.urls || {},
        });

        // If single URL, set it directly
        const entries = Object.entries(data.urls || {});
        if (entries.length === 1) {
          setWebUrl(entries[0][1]);
        }

        setIsCheckingAuth(false);
      } else {
        console.warn('[AutoLogin] Login failed:', data.message);
        // Clear invalid credentials
        await clearUserData();
        setIsCheckingAuth(false);
      }
    } catch (err) {
      console.error('[AutoLogin] Network error:', err);
      setIsCheckingAuth(false);
    }
  };

  const handleLoginSuccess = (userData) => {
    setUser(userData);
    const entries = Object.entries(userData.urls || {});
    if (entries.length === 1) {
      setWebUrl(entries[0][1]);
    }
  };

  const handleLogout = useCallback(async () => {
    // Unregister device token from backend
    if (user && currentUrl) {
      const userId = user.loginId || user.username; // Use loginId (email/username from login)
      await unregisterDevice(currentUrl, userId);
    }

    // Clear local user data
    await clearUserData();

    // Reset state
    setUser(null);
    setWebUrl(null);
  }, [user, currentUrl]);

  const isMultiUrl = Object.keys(user?.urls || {}).length > 1;

  // Show loading screen while checking for saved credentials
  if (isCheckingAuth) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#4a90e2" />
        <Text style={styles.loadingText}>Loading...</Text>
      </View>
    );
  }

  if (!user) {
    return <LoginScreen onLoginSuccess={handleLoginSuccess} />;
  }

  if (!webUrl) {
    return (
      <URLSelectorScreen
        user={user}
        urls={user.urls}
        onSelect={setWebUrl}
        onLogout={handleLogout}
      />
    );
  }

  return (
    <HomeScreen
      user={user}
      url={webUrl}
      isMultiUrl={isMultiUrl}
      onBackToSelector={() => setWebUrl(null)}
      onLogout={handleLogout}
    />
  );
}

export default function App() {
  return (
    <ApiConfigProvider>
      <AppProvider>
        <RootNavigator />
      </AppProvider>
    </ApiConfigProvider>
  );
}

const styles = StyleSheet.create({
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: '#f5f7fa',
  },
  loadingText: {
    marginTop: 16,
    fontSize: 16,
    color: '#666',
    fontWeight: '600',
  },
});
