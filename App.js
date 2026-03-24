import { useState, useCallback, useEffect } from 'react';
import { Alert, View, ActivityIndicator, StyleSheet, Text } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { AppProvider } from './utils/AppContext';
import { ApiConfigProvider, useApiConfig } from './utils/ApiConfig';
import { unregisterDevice, clearUserData } from './utils/logout';
import { hasStoredCredentials, getStoredCredentials, authenticateWithDevice } from './utils/secureAuth';
import { getDeviceId } from './utils/deviceId';
import { registerForPushNotifications, registerTokenWithBackend } from './utils/notifications';
import { checkAllPermissions } from './utils/permissionChecker';
import { collectDeviceMetadata, sendAuditLog } from './utils/auditLogger';
import PermissionsScreen from './screens/PermissionsScreen';
import LoginScreen from './screens/LoginScreen';
import URLSelectorScreen from './screens/URLSelectorScreen';
import HomeScreen from './screens/HomeScreen';

function RootNavigator() {
  const { currentUrl } = useApiConfig();
  const [user, setUser] = useState(null);
  const [webUrl, setWebUrl] = useState(null);
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  const [permissionsGranted, setPermissionsGranted] = useState(false);
  const [checkingPermissions, setCheckingPermissions] = useState(true);
  const [deniedPermissions, setDeniedPermissions] = useState([]);

  // Check actual permission status on every app launch
  useEffect(() => {
    checkPermissionsStatus();
  }, []);

  // Check for saved credentials after permissions are handled
  useEffect(() => {
    if (permissionsGranted) {
      checkSavedCredentials();
    }
  }, [permissionsGranted]);

  const checkPermissionsStatus = async () => {
    try {
      console.log('[Permissions] Checking actual permission status...');
      const { allGranted, denied } = await checkAllPermissions();

      if (allGranted) {
        // All permissions granted - skip permissions screen
        console.log('[Permissions] All permissions granted, proceeding');
        setPermissionsGranted(true);
      } else {
        // Some permissions denied - show permissions screen with ONLY denied ones
        console.log('[Permissions] Missing permissions:', denied);
        setDeniedPermissions(denied);
      }

      setCheckingPermissions(false);
    } catch (err) {
      console.error('[Permissions] Check status error:', err);
      // On error, assume permissions granted to avoid blocking
      setPermissionsGranted(true);
      setCheckingPermissions(false);
    }
  };

  const handlePermissionsComplete = async (results) => {
    console.log('[Permissions] User completed permissions:', results);

    // Re-check permission status to see if all are now granted
    const { allGranted, denied } = await checkAllPermissions();

    if (allGranted) {
      // All granted now - proceed to app
      console.log('[Permissions] All permissions now granted');
      setPermissionsGranted(true);
    } else {
      // Still some denied - proceed anyway but will show again next launch
      console.log('[Permissions] Some still denied, but allowing app access');
      setDeniedPermissions(denied);
      setPermissionsGranted(true);
    }
  };

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

        // Collect and send audit log with comprehensive device metadata
        setTimeout(async () => {
          try {
            console.log('[AutoLogin] Collecting device metadata for audit log...');
            const metadata = await collectDeviceMetadata();
            await sendAuditLog(currentUrl, email, deviceId, 'auto-login', metadata);
          } catch (err) {
            console.error('[AutoLogin] Audit log error:', err);
            // Don't block auto-login if audit log fails
          }
        }, 500); // Start audit log collection after 500ms

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

  // Show loading screen while checking permissions status
  if (checkingPermissions) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#4a90e2" />
        <Text style={styles.loadingText}>Loading...</Text>
      </View>
    );
  }

  // Show permissions screen on first launch or if some permissions are denied
  if (!permissionsGranted) {
    return (
      <PermissionsScreen
        onComplete={handlePermissionsComplete}
        deniedOnly={deniedPermissions}
      />
    );
  }

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
    <SafeAreaProvider>
      <ApiConfigProvider>
        <AppProvider>
          <RootNavigator />
        </AppProvider>
      </ApiConfigProvider>
    </SafeAreaProvider>
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
