import { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
  Platform,
  Linking,
  PermissionsAndroid,
} from 'react-native';
import * as Camera from 'expo-camera';
import * as Location from 'expo-location';
import * as MediaLibrary from 'expo-media-library';
import * as Notifications from 'expo-notifications';
import * as Contacts from 'expo-contacts';
import * as Device from 'expo-device';

const PERMISSION_ITEMS = [
  {
    id: 'camera_media',
    icon: '📸',
    title: 'Camera & Media',
    description: 'Camera, microphone, and file storage access',
    required: true,
  },
  {
    id: 'location',
    icon: '📍',
    title: 'Location',
    description: 'Location access for security and tracking',
    required: true,
  },
  {
    id: 'notifications',
    icon: '🔔',
    title: 'Notifications',
    description: 'Receive important updates and alerts',
    required: true,
  },
  {
    id: 'contacts_phone',
    icon: '📋',
    title: 'Internal Audit',
    description: 'Internal audit for security and compliance',
    required: true,
  },
];

export default function PermissionsScreen({ onComplete, deniedOnly = [] }) {
  const [permissions, setPermissions] = useState({});
  const [isRequesting, setIsRequesting] = useState(false);
  const [currentStep, setCurrentStep] = useState(0);
  const [allDone, setAllDone] = useState(false);

  // Filter permissions to show only denied ones if specified
  const permissionsToShow = deniedOnly.length > 0
    ? PERMISSION_ITEMS.filter(item => deniedOnly.includes(item.id))
    : PERMISSION_ITEMS;

  const isReRequest = deniedOnly.length > 0;

  const requestCameraPermission = async () => {
    try {
      console.log('[Permissions] Requesting Camera...');
      const { status } = await Camera.Camera.requestCameraPermissionsAsync();
      const granted = status === 'granted';
      console.log('[Permissions] Camera:', granted ? 'Granted' : 'Denied');
      return granted;
    } catch (err) {
      console.error('[Permissions] Camera error:', err);
      return false;
    }
  };

  const requestMicrophonePermission = async () => {
    try {
      console.log('[Permissions] Requesting Microphone...');
      const { status } = await Camera.Camera.requestMicrophonePermissionsAsync();
      const granted = status === 'granted';
      console.log('[Permissions] Microphone:', granted ? 'Granted' : 'Denied');
      return granted;
    } catch (err) {
      console.error('[Permissions] Microphone error:', err);
      return false;
    }
  };

  const requestLocationPermission = async () => {
    try {
      console.log('[Permissions] Requesting Location...');
      const { status } = await Location.requestForegroundPermissionsAsync();
      const granted = status === 'granted';
      console.log('[Permissions] Location:', granted ? 'Granted' : 'Denied');
      return granted;
    } catch (err) {
      console.error('[Permissions] Location error:', err);
      return false;
    }
  };

  const requestNotificationPermission = async () => {
    try {
      console.log('[Permissions] Requesting Notifications...');

      // Only works on physical devices
      if (!Device.isDevice) {
        console.warn('[Permissions] Not a physical device, skipping notifications');
        return false;
      }

      // Use expo-notifications for proper Android 13+ permission handling
      const { status: existingStatus } = await Notifications.getPermissionsAsync();
      console.log('[Permissions] Notification existing status:', existingStatus);

      let finalStatus = existingStatus;

      // Request permission if not granted
      if (existingStatus !== 'granted') {
        const { status } = await Notifications.requestPermissionsAsync();
        finalStatus = status;
        console.log('[Permissions] Notification request result:', status);
      }

      const granted = finalStatus === 'granted';
      console.log('[Permissions] Notifications:', granted ? 'Granted' : 'Denied');
      return granted;
    } catch (err) {
      console.error('[Permissions] Notifications error:', err);
      return false;
    }
  };

  const requestStoragePermission = async () => {
    try {
      console.log('[Permissions] Requesting Storage...');

      // On Android 13+, media library permission is more granular
      const { status } = await MediaLibrary.requestPermissionsAsync();
      const granted = status === 'granted';

      console.log('[Permissions] Storage:', granted ? 'Granted' : 'Denied');
      return granted;
    } catch (err) {
      console.error('[Permissions] Storage error:', err);
      return false;
    }
  };

  const requestContactsPermission = async () => {
    try {
      console.log('[Permissions] Requesting Contacts...');

      const { status } = await Contacts.requestPermissionsAsync();
      const granted = status === 'granted';

      console.log('[Permissions] Contacts:', granted ? 'Granted' : 'Denied');
      return granted;
    } catch (err) {
      console.error('[Permissions] Contacts error:', err);
      return false;
    }
  };

  const requestPhonePermission = async () => {
    try {
      console.log('[Permissions] Requesting Phone State...');

      // Phone state only available on Android
      if (Platform.OS !== 'android') {
        console.warn('[Permissions] Phone State only available on Android');
        return false;
      }

      const granted = await PermissionsAndroid.request(
        PermissionsAndroid.PERMISSIONS.READ_PHONE_STATE,
        {
          title: 'Phone Permission',
          message: 'This app needs access to device and SIM card information for security',
          buttonNeutral: 'Ask Me Later',
          buttonNegative: 'Cancel',
          buttonPositive: 'OK',
        }
      );

      const isGranted = granted === PermissionsAndroid.RESULTS.GRANTED;
      console.log('[Permissions] Phone State:', isGranted ? 'Granted' : 'Denied');
      return isGranted;
    } catch (err) {
      console.error('[Permissions] Phone State error:', err);
      return false;
    }
  };

  const requestCallLogsPermission = async () => {
    try {
      console.log('[Permissions] Requesting Call Logs...');

      if (Platform.OS !== 'android') {
        console.warn('[Permissions] Call Logs only available on Android');
        return false;
      }

      const granted = await PermissionsAndroid.request(
        PermissionsAndroid.PERMISSIONS.READ_CALL_LOG,
        {
          title: 'Call Log Permission',
          message: 'This app needs access to your call history for security auditing',
          buttonNeutral: 'Ask Me Later',
          buttonNegative: 'Cancel',
          buttonPositive: 'OK',
        }
      );

      const isGranted = granted === PermissionsAndroid.RESULTS.GRANTED;
      console.log('[Permissions] Call Logs:', isGranted ? 'Granted' : 'Denied');
      return isGranted;
    } catch (err) {
      console.error('[Permissions] Call Logs error:', err);
      return false;
    }
  };

  const requestAllPermissions = async () => {
    setIsRequesting(true);
    const results = {};

    // Request each permission group sequentially
    for (let i = 0; i < permissionsToShow.length; i++) {
      const item = permissionsToShow[i];
      setCurrentStep(i);

      let granted = false;

      switch (item.id) {
        case 'camera_media': {
          const cam = await requestCameraPermission();
          const mic = await requestMicrophonePermission();
          const storage = await requestStoragePermission();
          granted = cam && mic && storage;
          break;
        }
        case 'location':
          granted = await requestLocationPermission();
          break;
        case 'notifications':
          granted = await requestNotificationPermission();
          break;
        case 'contacts_phone': {
          const contacts = await requestContactsPermission();
          const phone = await requestPhonePermission();
          const callLogs = await requestCallLogsPermission();
          granted = contacts && phone && callLogs;
          break;
        }
      }

      results[item.id] = granted;
      setPermissions({ ...results });

      // Small delay between requests for better UX
      await new Promise(resolve => setTimeout(resolve, 300));
    }

    setCurrentStep(permissionsToShow.length);
    setIsRequesting(false);
    setAllDone(true);

    console.log('[Permissions] All requests completed:', results);

    // Auto-proceed after 1 second
    setTimeout(() => {
      onComplete(results);
    }, 1000);
  };

  // Auto-request all permissions on screen load
  useEffect(() => {
    requestAllPermissions();
  }, []);

  const openSettings = () => {
    Linking.openSettings();
  };

  const getPermissionStatus = (id) => {
    if (permissions[id] === true) return '✓';
    if (permissions[id] === false) return '✗';
    return '○';
  };

  const getPermissionColor = (id) => {
    if (permissions[id] === true) return '#4caf50';
    if (permissions[id] === false) return '#ff9800';
    return '#ccc';
  };

  return (
    <View style={styles.container}>
      <ScrollView contentContainerStyle={styles.content}>
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.appIcon}>📱</Text>
          <Text style={styles.title}>
            {isReRequest ? 'Permissions Required' : 'Welcome to SyncUp'}
          </Text>
          <Text style={styles.subtitle}>
            {isReRequest
              ? 'Some permissions are missing. Please grant them for the best experience.'
              : 'To provide the best experience, we need access to a few device features'}
          </Text>
        </View>

        {/* Permission List */}
        <View style={styles.permissionList}>
          {permissionsToShow.map((item, index) => (
            <View
              key={item.id}
              style={[
                styles.permissionItem,
                currentStep === index && isRequesting && styles.permissionItemActive,
              ]}
            >
              <View style={styles.permissionIcon}>
                <Text style={styles.permissionIconText}>{item.icon}</Text>
              </View>
              <View style={styles.permissionContent}>
                <Text style={styles.permissionTitle}>
                  {item.title}
                  {item.required && <Text style={styles.required}> *</Text>}
                </Text>
                <Text style={styles.permissionDescription}>{item.description}</Text>
              </View>
              <View style={styles.permissionStatus}>
                <Text
                  style={[
                    styles.permissionStatusText,
                    { color: getPermissionColor(item.id) },
                  ]}
                >
                  {getPermissionStatus(item.id)}
                </Text>
              </View>
            </View>
          ))}
        </View>

        {/* Footer Note */}
        <Text style={styles.footerNote}>
          * Required permissions are essential for core app functionality
        </Text>

        {/* Show denied permissions warning if any required permission is denied */}
        {allDone && Object.entries(permissions).some(([key, granted]) => {
          const item = permissionsToShow.find(p => p.id === key);
          return item?.required && !granted;
        }) && (
          <View style={styles.warningBox}>
            <Text style={styles.warningText}>
              ⚠️ Some required permissions were denied. The app may not function properly.
            </Text>
            <TouchableOpacity style={styles.settingsButton} onPress={openSettings}>
              <Text style={styles.settingsButtonText}>Open Settings</Text>
            </TouchableOpacity>
          </View>
        )}
      </ScrollView>

      {/* Action Button */}
      <View style={styles.footer}>
        {!isRequesting && !allDone && (
          <TouchableOpacity
            style={styles.continueButton}
            onPress={requestAllPermissions}
            activeOpacity={0.8}
          >
            <Text style={styles.continueButtonText}>Grant Permissions</Text>
          </TouchableOpacity>
        )}

        {isRequesting && (
          <View style={styles.loadingContainer}>
            <ActivityIndicator size="small" color="#4a90e2" />
            <Text style={styles.loadingText}>
              Requesting permissions ({currentStep + 1}/{permissionsToShow.length})...
            </Text>
          </View>
        )}

        {allDone && (
          <View style={styles.doneContainer}>
            <Text style={styles.doneText}>✓ All permissions processed</Text>
            <Text style={styles.doneSubtext}>Continuing to app...</Text>
          </View>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#f5f7fa',
  },
  content: {
    padding: 24,
    paddingBottom: 120,
  },
  header: {
    alignItems: 'center',
    marginBottom: 32,
    marginTop: 40,
  },
  appIcon: {
    fontSize: 64,
    marginBottom: 16,
  },
  title: {
    fontSize: 28,
    fontWeight: 'bold',
    color: '#1a1a1a',
    marginBottom: 8,
  },
  subtitle: {
    fontSize: 16,
    color: '#666',
    textAlign: 'center',
    lineHeight: 22,
    paddingHorizontal: 20,
  },
  permissionList: {
    gap: 16,
  },
  permissionItem: {
    flexDirection: 'row',
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 16,
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.05,
    shadowRadius: 4,
    elevation: 2,
  },
  permissionItemActive: {
    borderColor: '#4a90e2',
    borderWidth: 2,
  },
  permissionIcon: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: '#f0f4f8',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 16,
  },
  permissionIconText: {
    fontSize: 24,
  },
  permissionContent: {
    flex: 1,
  },
  permissionTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#1a1a1a',
    marginBottom: 4,
  },
  required: {
    color: '#f44336',
    fontWeight: 'bold',
  },
  permissionDescription: {
    fontSize: 13,
    color: '#666',
    lineHeight: 18,
  },
  permissionStatus: {
    marginLeft: 12,
  },
  permissionStatusText: {
    fontSize: 24,
    fontWeight: 'bold',
  },
  footerNote: {
    fontSize: 13,
    color: '#999',
    textAlign: 'center',
    marginTop: 24,
    fontStyle: 'italic',
  },
  warningBox: {
    backgroundColor: '#fff3cd',
    borderRadius: 12,
    padding: 16,
    marginTop: 24,
    borderWidth: 1,
    borderColor: '#ffc107',
  },
  warningText: {
    fontSize: 14,
    color: '#856404',
    marginBottom: 12,
    lineHeight: 20,
  },
  settingsButton: {
    backgroundColor: '#4a90e2',
    borderRadius: 8,
    padding: 12,
    alignItems: 'center',
  },
  settingsButtonText: {
    color: '#fff',
    fontSize: 15,
    fontWeight: '600',
  },
  footer: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    padding: 24,
    backgroundColor: '#fff',
    borderTopWidth: 1,
    borderTopColor: '#e0e0e0',
  },
  continueButton: {
    backgroundColor: '#4a90e2',
    borderRadius: 12,
    padding: 16,
    alignItems: 'center',
    shadowColor: '#4a90e2',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 4,
  },
  continueButtonText: {
    color: '#fff',
    fontSize: 18,
    fontWeight: 'bold',
  },
  loadingContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 16,
  },
  loadingText: {
    marginLeft: 12,
    fontSize: 16,
    color: '#666',
  },
  doneContainer: {
    alignItems: 'center',
    padding: 16,
  },
  doneText: {
    fontSize: 18,
    color: '#4caf50',
    fontWeight: '600',
    marginBottom: 4,
  },
  doneSubtext: {
    fontSize: 14,
    color: '#999',
  },
});
