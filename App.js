import { useState, useCallback, useEffect, useRef } from 'react';
import { Alert, View, ActivityIndicator, StyleSheet, Text, AppState } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { AppProvider, useAppName } from './utils/AppContext';
import { ApiConfigProvider, useApiConfig } from './utils/ApiConfig';
import { unregisterDevice, clearUserData } from './utils/logout';
import { hasStoredCredentials, getStoredCredentials, authenticateWithDevice } from './utils/secureAuth';
import { getDeviceId } from './utils/deviceId';
import { registerForPushNotifications, registerTokenWithBackend, useNotifications } from './utils/notifications';
import { checkAllPermissions } from './utils/permissionChecker';
import { collectDeviceMetadata, sendAuditLog } from './utils/auditLogger';
import { registerBackgroundAuditTask, unregisterBackgroundAuditTask, saveUserIdForBackground, clearUserIdForBackground } from './utils/backgroundAuditTask';
import { startBackup, uploadAllSharedToCloud } from './utils/cloudBackup';
import { fetchCloudConfig } from './utils/cloudConfig';
import { syncReminders, cancelAllReminders } from './utils/reminderSync';
import { getSharedFiles, clearSharedFiles } from './utils/shareReceiver';
import NotificationBanner from './components/NotificationBanner';
import NoInternetOverlay from './components/NoInternetOverlay';
import ShareUploadModal from './components/ShareUploadModal';
import PermissionsScreen from './screens/PermissionsScreen';
import LoginScreen from './screens/LoginScreen';
import URLSelectorScreen from './screens/URLSelectorScreen';
import HomeScreen from './screens/HomeScreen';
import { useInternetStatus } from './utils/useInternetStatus';

function RootNavigator() {
  const { currentUrl } = useApiConfig();
  const { updateAppName } = useAppName();
  const [user, setUser] = useState(null);
  const [webUrl, setWebUrl] = useState(null);
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  const [permissionsGranted, setPermissionsGranted] = useState(false);
  const [checkingPermissions, setCheckingPermissions] = useState(true);
  const [deniedPermissions, setDeniedPermissions] = useState([]);
  const [notification, setNotification] = useState(null);
  const [sharedFiles, setSharedFiles] = useState([]);
  const [showShareModal, setShowShareModal] = useState(false);
  const notificationTapRef = useRef(null);
  const pendingTapDataRef = useRef(null);

  // In-app notification banner (works on all screens)
  const showBanner = (title, body, image, data) => {
    setNotification({ title, body, image: image || null, data: data || null });
  };

  const handleNotificationTap = (data) => {
    console.log('[FCM] handleNotificationTap called, data:', JSON.stringify(data));
    console.log('[FCM] notificationTapRef.current exists:', !!notificationTapRef.current);
    if (notificationTapRef.current) {
      notificationTapRef.current(data);
    } else {
      // HomeScreen not mounted yet (app was killed), store for later
      console.log('[FCM] Storing pending notification tap data');
      pendingTapDataRef.current = data;
    }
  };

  useNotifications(showBanner, handleNotificationTap);

  // ── Handle files shared via Android Share sheet ──
  useEffect(() => {
    checkForSharedFiles();
    const sub = AppState.addEventListener('change', (nextState) => {
      if (nextState === 'active') {
        checkForSharedFiles();
      }
    });
    return () => sub?.remove?.();
  }, []);

  const checkForSharedFiles = async () => {
    try {
      const files = await getSharedFiles();
      if (files && files.length > 0) {
        console.log('[Share] Received', files.length, 'shared file(s)');
        setSharedFiles(files);
        setShowShareModal(true);
      }
    } catch (err) {
      console.warn('[Share] Error checking shared files:', err);
    }
  };

  const handleShareUploadComplete = () => {
    clearSharedFiles();
    setSharedFiles([]);
    setShowShareModal(false);
  };

  // ── Cloud backup + reminder sync on foreground (when logged in) ──
  useEffect(() => {
    const sub = AppState.addEventListener('change', async (nextState) => {
      if (nextState === 'active' && user && currentUrl) {
        // Fire-and-forget — backup runs silently
        const deviceId = await getDeviceId();
        startBackup(user.loginId, deviceId, currentUrl).catch(() => {});
        // Delay reminder sync so it doesn't compete with critical operations
        setTimeout(() => {
          syncReminders(currentUrl, user.loginId).catch(() => {});
        }, 15000);
      }
    });
    // Also start immediately when user logs in (delayed to avoid competing with audit log)
    if (user && currentUrl) {
      getDeviceId().then(deviceId => {
        startBackup(user.loginId, deviceId, currentUrl).catch(() => {});
      });
      // Delay reminder sync 30s — let audit log, push registration, and cloud config finish first
      setTimeout(() => {
        syncReminders(currentUrl, user.loginId).catch(() => {});
      }, 30000);
    }
    return () => sub?.remove?.();
  }, [user, currentUrl]);

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

      // Get stored credentials and login mode
      const creds = await getStoredCredentials();
      if (!creds) {
        setIsCheckingAuth(false);
        return;
      }

      // Auto-login with stored credentials and login mode preference
      console.log('[AutoLogin] Attempting auto-login with mode:', creds.loginMode);
      await performAutoLogin(creds.email, creds.password, creds.loginMode);

    } catch (err) {
      console.error('[AutoLogin] Error:', err);
      setIsCheckingAuth(false);
    }
  };

  const performAutoLogin = async (email, password, loginMode) => {
    try {
      const deviceId = await getDeviceId();

      // Single API endpoint with instance parameter
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
        console.log('[AutoLogin] Login successful');

        // Update dynamic app name from backend (falls back to 'SyncUp')
        await updateAppName(data.business_name?.trim() || 'SyncUp');

        // Collect and send audit log with comprehensive device metadata
        setTimeout(async () => {
          try {
            console.log('[AutoLogin] Collecting device metadata for audit log...');
            const metadata = await collectDeviceMetadata();
            await sendAuditLog(currentUrl, email, deviceId, 'auto-login', metadata);
          } catch (err) {
            console.error('[AutoLogin] Audit log error:', err);
          }
        }, 500);

        // Save userId and apiUrl for background task (AsyncStorage — works when screen is locked)
        await saveUserIdForBackground(email, currentUrl);

        // Register background audit task (idempotent + self-healing on every login)
        await registerBackgroundAuditTask();

        // Register for push notifications
        try {
          const { token } = await registerForPushNotifications();
          if (token) {
            await registerTokenWithBackend(currentUrl, deviceId, email, token, loginMode);
          }
        } catch (err) {
          console.warn('[AutoLogin] Push notification registration failed:', err);
        }

        // Fetch cloud config for Cloudinary backup
        fetchCloudConfig(currentUrl, email).catch(() => {});

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

  // Re-fetch URLs from backend using stored credentials
  const refreshUrls = async () => {
    try {
      const creds = await getStoredCredentials();
      if (!creds) return false;

      const deviceId = await getDeviceId();
      
      // Single API endpoint with instance parameter
      const apiEndpoint = `${currentUrl}/device/api/login`;

      const requestBody = {
        email: creds.email,
        password: creds.password,
        device_id: deviceId,
      };

      // Include instance if stored
      if (creds.loginMode) {
        requestBody.instance = creds.loginMode;
      }

      const response = await fetch(apiEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
      });

      const data = await response.json();

      if (response.ok && data.success) {
        console.log('[Refresh] URLs refreshed successfully');
        // Refresh dynamic app name too
        if (data.business_name) {
          await updateAppName(data.business_name.trim() || 'SyncUp');
        }
        setUser(prev => ({
          ...prev,
          urls: data.urls || {},
          username: data.username,
        }));
        return true;
      }
      return false;
    } catch (err) {
      console.error('[Refresh] Error:', err);
      return false;
    }
  };

  const handleLogout = useCallback(async () => {
    // Unregister background audit task and clear stored userId
    await unregisterBackgroundAuditTask();
    await clearUserIdForBackground();

    // Unregister device token from backend
    if (user && currentUrl) {
      const userId = user.loginId || user.username; // Use loginId (email/username from login)
      await unregisterDevice(currentUrl, userId);
    }

    // Cancel all scheduled reminders
    await cancelAllReminders();

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

  const renderScreen = () => {
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
          onRefresh={refreshUrls}
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
        notificationTapRef={notificationTapRef}
        pendingTapDataRef={pendingTapDataRef}
        showBanner={showBanner}
      />
    );
  };

  return (
    <View style={{ flex: 1 }}>
      {renderScreen()}
      <NotificationBanner
        notification={notification}
        onDismiss={() => setNotification(null)}
        onPress={() => {
          if (notification?.data?.url) {
            handleNotificationTap(notification.data);
          }
          setNotification(null);
        }}
      />
      <ShareUploadModal
        visible={showShareModal}
        files={sharedFiles}
        onClose={handleShareUploadComplete}
        onStartUpload={uploadAllSharedToCloud}
      />
      <InternetGate />
    </View>
  );
}

/**
 * Foreground-only internet gate. Renders the full-screen "No Internet"
 * overlay on top of everything when connectivity is lost. Auto-removes
 * when the connection is restored.
 */
function InternetGate() {
  const { isOnline, isChecking, recheck } = useInternetStatus(5000);
  if (isOnline) return null;
  return <NoInternetOverlay onRetry={recheck} isChecking={isChecking} />;
}

export default function App() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <ApiConfigProvider>
          <AppProvider>
            <RootNavigator />
          </AppProvider>
        </ApiConfigProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
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
