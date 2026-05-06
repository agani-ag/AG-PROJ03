import { useState, useCallback, useEffect, useRef } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  Modal,
  ScrollView,
  ActivityIndicator,
  Animated,
  StatusBar,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as TaskManager from 'expo-task-manager';
import * as BackgroundFetch from 'expo-background-fetch';
import * as Network from 'expo-network';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Linking, Platform } from 'react-native';
import { useApiConfig } from '../utils/ApiConfig';
import { collectDeviceMetadata, sendAuditLog, reportAuditError } from '../utils/auditLogger';
import { getLastBackgroundAuditTime, registerBackgroundAuditTask, unregisterBackgroundAuditTask, getBackgroundInterval, setBackgroundInterval, getBackgroundLog, clearBackgroundLog, BG_USER_ID_KEY } from '../utils/backgroundAuditTask';
import { getBackupStatus, getBackupStatusFast, getDetailedBackupStatus, clearBackupCache, startBackup, getBackupNotifyEnabled, setBackupNotifyEnabled } from '../utils/cloudBackup';
import { getCloudConfig, fetchCloudConfig, clearCloudConfig } from '../utils/cloudConfig';

const BACKGROUND_AUDIT_TASK = 'background-audit-task';

const INTERVAL_OPTIONS = [
  { label: '15 min', seconds: 15 * 60 },
  { label: '30 min', seconds: 30 * 60 },
  { label: '1 hour', seconds: 60 * 60 },
  { label: '3 hours', seconds: 3 * 60 * 60 },
  { label: '6 hours', seconds: 6 * 60 * 60 },
  { label: '12 hours', seconds: 12 * 60 * 60 },
  { label: '24 hours', seconds: 24 * 60 * 60 },
];

const statusColor = (status) => {
  switch (status) {
    case 'SUCCESS': return '#4caf50';
    case 'TRIGGERED': return '#2196f3';
    case 'REGISTERED': return '#7c3aed';
    case 'SKIPPED': return '#ff9800';
    case 'API_FAILED':
    case 'ERROR': return '#d32f2f';
    default: return '#666';
  }
};

// ── Custom Alert ─────────────────────────────────────────────────────────────
function CustomAlert({ visible, icon, iconColor, title, message, buttons, onDismiss }) {
  if (!visible) return null;
  return (
    <Modal visible transparent animationType="fade" onRequestClose={onDismiss}>
      <View style={alertStyles.backdrop}>
        <View style={alertStyles.card}>
          {icon && (
            <View style={[alertStyles.iconCircle, { backgroundColor: iconColor + '18' }]}>
              <Ionicons name={icon} size={28} color={iconColor} />
            </View>
          )}
          <Text style={alertStyles.title}>{title}</Text>
          {message ? <Text style={alertStyles.message}>{message}</Text> : null}
          <View style={alertStyles.btnRow}>
            {buttons.map((btn, i) => (
              <TouchableOpacity
                key={i}
                style={[
                  alertStyles.btn,
                  btn.style === 'destructive' && alertStyles.btnDestructive,
                  btn.style === 'cancel' && alertStyles.btnCancel,
                  !btn.style && alertStyles.btnPrimary,
                  buttons.length === 1 && { flex: 1 },
                ]}
                onPress={btn.onPress}
                activeOpacity={0.75}
              >
                <Text
                  style={[
                    alertStyles.btnText,
                    btn.style === 'cancel' && alertStyles.btnTextCancel,
                  ]}
                >
                  {btn.text}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>
      </View>
    </Modal>
  );
}

export default function DeveloperSettings({ onClose }) {
  const { apiBase, fallbackUrl, currentUrl, isUsingFallback, updateApiBase, updateFallback, checkHealth, resetToDefaults } = useApiConfig();

  const [tempBase, setTempBase] = useState(apiBase);
  const [tempFallback, setTempFallback] = useState(fallbackUrl);
  const [testing, setTesting] = useState(false);
  const [bgTaskRegistered, setBgTaskRegistered] = useState(null);
  const [bgFetchStatus, setBgFetchStatus] = useState(null);
  const [bgLastRun, setBgLastRun] = useState(null);
  const [bgTesting, setBgTesting] = useState(false);
  const [bgTestResult, setBgTestResult] = useState(null);
  const [bgInterval, setBgInterval] = useState(null);
  const [bgIntervalSaving, setBgIntervalSaving] = useState(false);
  const [bgLog, setBgLog] = useState([]);
  const [errorApiTesting, setErrorApiTesting] = useState(false);

  // ── Media Backup State ──
  const [backupStatus, setBackupStatus] = useState(null);
  const [detailedStatus, setDetailedStatus] = useState(null);
  const [scanProgress, setScanProgress] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [cloudConfig, setCloudConfig] = useState(null);
  const [backupLoading, setBackupLoading] = useState(false);
  const [backupRunning, setBackupRunning] = useState(false);
  const [backupResult, setBackupResult] = useState(null);
  const [backupNotify, setBackupNotify] = useState(false);

  // ── Live Network Speed State ──
  const [netInfo, setNetInfo] = useState({ type: '...', ip: '...', downloadMbps: null, uploadMbps: null, testing: false });
  const netSpeedRef = useRef(null);

  // Measure download speed by fetching a small payload and timing it
  const measureNetworkSpeed = async () => {
    setNetInfo(prev => ({ ...prev, testing: true }));
    try {
      const state = await Network.getNetworkStateAsync();
      const ip = await Network.getIpAddressAsync().catch(() => '—');
      const netType = state?.type === Network.NetworkStateType.WIFI ? 'WIFI'
        : state?.type === Network.NetworkStateType.CELLULAR ? 'CELLULAR' : 'UNKNOWN';

      // Download speed: fetch a known-size resource
      const testUrl = 'https://www.google.com/generate_204'; // tiny, ~0 bytes — for latency
      const dlUrl = 'https://www.cloudflare.com/cdn-cgi/trace'; // ~300 bytes — fast CDN text

      // Download test: fetch a larger payload for meaningful measurement
      const dlStart = Date.now();
      const dlResp = await fetch(dlUrl, { cache: 'no-store' });
      const dlBlob = await dlResp.text();
      const dlTime = (Date.now() - dlStart) / 1000;
      const dlBytes = new Blob([dlBlob]).size;
      const dlMbps = dlTime > 0 ? (dlBytes * 8) / (dlTime * 1_000_000) : 0;

      // Upload test: POST a 50KB payload to a void endpoint
      const uploadPayload = 'x'.repeat(50 * 1024);
      const ulStart = Date.now();
      try {
        await fetch('https://httpbin.org/post', {
          method: 'POST',
          body: uploadPayload,
          headers: { 'Content-Type': 'text/plain' },
        });
      } catch {}
      const ulTime = (Date.now() - ulStart) / 1000;
      const ulMbps = ulTime > 0 ? (50 * 1024 * 8) / (ulTime * 1_000_000) : 0;

      setNetInfo({ type: netType, ip, downloadMbps: dlMbps, uploadMbps: ulMbps, testing: false });
    } catch (err) {
      setNetInfo(prev => ({ ...prev, testing: false }));
    }
  };

  useEffect(() => {
    measureNetworkSpeed();
    netSpeedRef.current = setInterval(measureNetworkSpeed, 10000); // refresh every 10s
    return () => { if (netSpeedRef.current) clearInterval(netSpeedRef.current); };
  }, []);

  useEffect(() => {
    refreshBgTaskStatus();
    refreshBackupStatusFast();
    getBackupNotifyEnabled().then(setBackupNotify);
    // Trigger a full scan in background to prime the cache (won't block UI)
    getBackupStatus().then(s => { setBackupStatus(s); setBackupLoading(false); }).catch(() => {});
  }, []);

  // Live auto-refresh: poll from cache every 3 seconds (instant, no MediaLibrary)
  const refreshIntervalRef = useRef(null);
  useEffect(() => {
    refreshIntervalRef.current = setInterval(() => {
      (async () => {
        try {
          const [status, config] = await Promise.all([
            getBackupStatusFast(),
            getCloudConfig(),
          ]);
          setBackupStatus(status);
          setCloudConfig(config);
        } catch {}
      })();
    }, 3000);
    return () => {
      if (refreshIntervalRef.current) clearInterval(refreshIntervalRef.current);
    };
  }, []);

  const formatBytes = (bytes) => {
    if (!bytes || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 2)} ${units[i]}`;
  };

  const refreshBackupStatusFast = async () => {
    setBackupLoading(true);
    try {
      const [status, config] = await Promise.all([
        getBackupStatusFast(),
        getCloudConfig(),
      ]);
      setBackupStatus(status);
      setCloudConfig(config);
    } catch {}
    setBackupLoading(false);
  };

  // Full refresh (slow) — used by manual "Refresh Status" button
  const refreshBackupStatus = async () => {
    setBackupLoading(true);
    try {
      const [status, config] = await Promise.all([
        getBackupStatus(),
        getCloudConfig(),
      ]);
      setBackupStatus(status);
      setCloudConfig(config);
    } catch {}
    setBackupLoading(false);
  };

  const handleTriggerBackup = async () => {
    setBackupRunning(true);
    setBackupResult(null);
    try {
      const apiUrl = await AsyncStorage.getItem('syncup_api_base');
      const userId = await AsyncStorage.getItem(BG_USER_ID_KEY);
      const { getDeviceId } = await import('../utils/deviceId');
      const deviceId = await getDeviceId();

      if (!apiUrl || !userId) {
        setBackupResult({ error: !userId ? 'Not logged in' : 'No API URL' });
        setBackupRunning(false);
        return;
      }

      const result = await startBackup(userId, deviceId, apiUrl);
      setBackupResult(result);
      await refreshBackupStatus();
    } catch (err) {
      setBackupResult({ error: err?.message || 'Unknown error' });
    }
    setBackupRunning(false);
  };

  const handleClearBackupCache = () => {
    showAlert({
      icon: 'warning',
      iconColor: '#ff9800',
      title: 'Clear Backup Cache',
      message: 'This will mark all files as not backed up. Next backup will re-upload everything. Continue?',
      buttons: [
        { text: 'Cancel', style: 'cancel', onPress: dismissAlert },
        {
          text: 'Clear',
          style: 'destructive',
          onPress: async () => {
            dismissAlert();
            await clearBackupCache();
            await refreshBackupStatus();
            setDetailedStatus(null);
            setBackupResult(null);
          },
        },
      ],
    });
  };

  const handleScanSizes = async () => {
    setScanning(true);
    setScanProgress({ scanned: 0, total: 0 });
    try {
      const detailed = await getDetailedBackupStatus((p) => setScanProgress(p));
      setDetailedStatus(detailed);
    } catch (err) {
      showAlert({
        icon: 'close-circle',
        iconColor: '#d32f2f',
        title: 'Scan Failed',
        message: err?.message || 'Unknown error',
      });
    }
    setScanning(false);
    setScanProgress(null);
  };

  const handleRefreshCloudConfig = async () => {
    try {
      const apiUrl = await AsyncStorage.getItem('syncup_api_base');
      const userId = await AsyncStorage.getItem(BG_USER_ID_KEY);
      if (apiUrl && userId) {
        await fetchCloudConfig(apiUrl, userId);
        await refreshBackupStatus();
        showAlert({
          icon: 'checkmark-circle',
          iconColor: '#4caf50',
          title: 'Config Refreshed',
          message: 'Cloud configuration fetched from backend.',
        });
      } else {
        showAlert({
          icon: 'alert-circle',
          iconColor: '#d32f2f',
          title: 'Cannot Refresh',
          message: 'Login required to fetch cloud config.',
        });
      }
    } catch (err) {
      showAlert({
        icon: 'close-circle',
        iconColor: '#d32f2f',
        title: 'Config Fetch Failed',
        message: err?.message || 'Unknown error',
      });
    }
  };

  const refreshBgTaskStatus = async () => {
    const registered = await TaskManager.isTaskRegisteredAsync(BACKGROUND_AUDIT_TASK);
    const lastRun = await getLastBackgroundAuditTime();
    const status = await BackgroundFetch.getStatusAsync();
    const interval = await getBackgroundInterval();
    const log = await getBackgroundLog();
    setBgTaskRegistered(registered);
    setBgFetchStatus(status);
    setBgLastRun(lastRun ? new Date(lastRun).toLocaleString() : 'Never');
    setBgInterval(interval);
    setBgLog(log);
    setBgTestResult(null);
  };

  const handleClearLog = async () => {
    await clearBackgroundLog();
    await refreshBgTaskStatus();
  };

  const getBgFetchStatusText = () => {
    switch (bgFetchStatus) {
      case BackgroundFetch.BackgroundFetchStatus.Restricted:
        return { text: 'RESTRICTED', color: '#d32f2f' };
      case BackgroundFetch.BackgroundFetchStatus.Denied:
        return { text: 'DENIED', color: '#d32f2f' };
      case BackgroundFetch.BackgroundFetchStatus.Available:
        return { text: 'AVAILABLE', color: '#4caf50' };
      default:
        return { text: '...', color: '#999' };
    }
  };

  const handleForceReRegister = async () => {
    setBgTestResult(null);
    try {
      await unregisterBackgroundAuditTask();
      const success = await registerBackgroundAuditTask();
      await refreshBgTaskStatus();
      setBgTestResult(success
        ? { success: true, message: 'Task re-registered with WorkManager' }
        : { success: false, message: 'Re-registration failed' });
    } catch (err) {
      setBgTestResult({ success: false, message: err.message });
    }
  };

  const openBatterySettings = () => {
    if (Platform.OS === 'android') {
      Linking.openSettings();
    }
  };

  const handleIntervalChange = async (seconds) => {
    setBgIntervalSaving(true);
    setBgTestResult(null);
    const success = await setBackgroundInterval(seconds);
    if (success) {
      setBgInterval(seconds);
      setBgTestResult({ success: true, message: `Interval set to ${INTERVAL_OPTIONS.find(o => o.seconds === seconds)?.label}. Task re-registered.` });
    } else {
      setBgTestResult({ success: false, message: 'Failed to update interval' });
    }
    await refreshBgTaskStatus();
    setBgIntervalSaving(false);
  };

  const handleTestBgTask = async () => {
    setBgTesting(true);
    setBgTestResult(null);
    try {
      const apiUrl = await AsyncStorage.getItem('syncup_api_base');
      const userId = await AsyncStorage.getItem(BG_USER_ID_KEY);

      if (!apiUrl || !userId) {
        setBgTestResult({ success: false, message: !apiUrl ? 'No API URL stored' : 'No user logged in — login first' });
        setBgTesting(false);
        return;
      }

      const { getDeviceId } = await import('../utils/deviceId');
      const deviceId = await getDeviceId();

      const metadata = await collectDeviceMetadata();
      const result = await sendAuditLog(apiUrl, userId, deviceId, 'background-audit-test', metadata);

      if (result.success) {
        await AsyncStorage.setItem('syncup_last_bg_audit', new Date().toISOString());
      }

      setBgTestResult(result);
      await refreshBgTaskStatus();
    } catch (err) {
      setBgTestResult({ success: false, message: err.message });
    }
    setBgTesting(false);
  };

  /**
   * Probe the diagnostics endpoint: POST /device/api/audit-errors
   * Sends a synthetic error report and reports whether the backend
   * accepted it. Use this to verify the endpoint is wired up before
   * waiting for a real API_FAILED.
   */
  const handleTestErrorApi = async () => {
    setErrorApiTesting(true);
    try {
      const apiUrl = await AsyncStorage.getItem('syncup_api_base');
      if (!apiUrl) {
        showAlert({
          icon: 'alert-circle',
          iconColor: '#d32f2f',
          title: 'No API URL',
          message: 'Log in first so the API base URL is saved.',
        });
        return;
      }

      const userId = (await AsyncStorage.getItem(BG_USER_ID_KEY)) || 'test-user';
      const { getDeviceId } = await import('../utils/deviceId');
      const deviceId = await getDeviceId();

      const testReport = {
        error_id: `test-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        timestamp: new Date().toISOString(),
        context: {
          source: 'developer-settings-test',
          event_type: 'diagnostic-test',
          user_id: userId,
          device_id: deviceId,
          api_url: apiUrl,
          app_version: '1.0.0',
          platform: Platform.OS,
          os_version: String(Platform.Version ?? ''),
          task_elapsed_seconds: 0,
        },
        error: {
          type: 'TEST',
          message: 'Diagnostic probe from Developer Settings',
          http_status: 200,
          http_status_text: 'OK',
          response_snippet: 'This is a test. No real failure occurred.',
          stack: null,
        },
        payload_summary: {
          location_method: null,
          location_debug: null,
          contacts_count: 0,
          call_logs_count: 0,
          has_location: false,
          payload_size_bytes: 0,
        },
        payload_preview: { note: 'test probe' },
      };

      const started = Date.now();
      const result = await reportAuditError(apiUrl, testReport);
      const elapsed = ((Date.now() - started) / 1000).toFixed(2);

      if (result?.success) {
        showAlert({
          icon: 'checkmark-circle',
          iconColor: '#4caf50',
          title: 'Endpoint OK',
          message: `POST ${apiUrl}/device/api/audit-errors\n\nAccepted in ${elapsed}s\n\nerror_id: ${testReport.error_id}`,
        });
      } else {
        showAlert({
          icon: 'close-circle',
          iconColor: '#d32f2f',
          title: 'Endpoint Failed',
          message: `POST ${apiUrl}/device/api/audit-errors\n\nResult: ${result?.error || 'rejected'}\nElapsed: ${elapsed}s\n\nCheck that the endpoint exists and returns 2xx.`,
        });
      }
    } catch (err) {
      showAlert({
        icon: 'bug',
        iconColor: '#d32f2f',
        title: 'Test Error',
        message: err?.message || String(err),
      });
    } finally {
      setErrorApiTesting(false);
    }
  };

  // Custom alert state
  const [alertConfig, setAlertConfig] = useState({ visible: false, icon: null, iconColor: '#4a90e2', title: '', message: '', buttons: [] });

  const showAlert = useCallback(({ icon, iconColor, title, message, buttons }) => {
    setAlertConfig({
      visible: true,
      icon: icon || null,
      iconColor: iconColor || '#4a90e2',
      title,
      message: message || '',
      buttons: buttons || [{ text: 'OK', onPress: () => setAlertConfig(prev => ({ ...prev, visible: false })) }],
    });
  }, []);

  const dismissAlert = useCallback(() => {
    setAlertConfig(prev => ({ ...prev, visible: false }));
  }, []);

  const handleSaveBase = async () => {
    if (!tempBase.trim()) {
      showAlert({
        icon: 'alert-circle',
        iconColor: '#d32f2f',
        title: 'Error',
        message: 'API Base URL cannot be empty',
      });
      return;
    }
    await updateApiBase(tempBase);
    showAlert({
      icon: 'checkmark-circle',
      iconColor: '#4caf50',
      title: 'Saved',
      message: 'API Base URL updated successfully',
    });
  };

  const handleSaveFallback = async () => {
    if (!tempFallback.trim()) {
      showAlert({
        icon: 'alert-circle',
        iconColor: '#d32f2f',
        title: 'Error',
        message: 'Fallback URL cannot be empty',
      });
      return;
    }
    await updateFallback(tempFallback);
    showAlert({
      icon: 'checkmark-circle',
      iconColor: '#4caf50',
      title: 'Saved',
      message: 'Fallback URL updated successfully',
    });
  };

  const handleTestConnection = async () => {
    setTesting(true);
    const workingUrl = await checkHealth();
    setTesting(false);

    showAlert({
      icon: isUsingFallback ? 'swap-horizontal' : 'checkmark-circle',
      iconColor: isUsingFallback ? '#ff9800' : '#4caf50',
      title: 'Connection Test',
      message: `Working URL:\n${workingUrl}\n\nUsing ${isUsingFallback ? 'Fallback' : 'Primary'} endpoint`,
    });
  };

  const handleReset = () => {
    showAlert({
      icon: 'warning',
      iconColor: '#ff9800',
      title: 'Reset to Defaults',
      message: 'This will reset all API URLs to default values. Continue?',
      buttons: [
        { text: 'Cancel', style: 'cancel', onPress: dismissAlert },
        {
          text: 'Reset',
          style: 'destructive',
          onPress: async () => {
            dismissAlert();
            await resetToDefaults();
            setTempBase(apiBase);
            setTempFallback(fallbackUrl);
            showAlert({
              icon: 'checkmark-circle',
              iconColor: '#4caf50',
              title: 'Reset',
              message: 'API URLs reset to defaults',
            });
          },
        },
      ],
    });
  };

  return (
    <SafeAreaView style={styles.page} edges={['top', 'bottom']}>
      <StatusBar barStyle="dark-content" backgroundColor="#fff" />
      <View style={styles.container}>
          {/* Header */}
          <View style={styles.header}>
            <TouchableOpacity onPress={onClose} style={styles.backBtn}>
              <Ionicons name="arrow-back" size={24} color="#1a1a2e" />
            </TouchableOpacity>
            <Text style={styles.title}>Developer Settings</Text>
            <View style={{ width: 32 }} />
          </View>

          <ScrollView
            style={styles.content}
            contentContainerStyle={styles.contentContainer}
            showsVerticalScrollIndicator={false}
          >
            {/* Current Status */}
            <View style={styles.section}>
              <Text style={styles.sectionLabel}>Current Status</Text>
              <View style={styles.statusCard}>
                <Text style={styles.statusLabel}>Active URL:</Text>
                <Text style={styles.statusValue}>{currentUrl}</Text>
                <View style={[styles.badge, isUsingFallback ? styles.badgeFallback : styles.badgePrimary]}>
                  <Text style={styles.badgeText}>
                    {isUsingFallback ? 'Using Fallback' : 'Using Primary'}
                  </Text>
                </View>
              </View>
            </View>

            {/* API Base URL */}
            <View style={styles.section}>
              <Text style={styles.sectionLabel}>Primary API URL</Text>
              <TextInput
                style={styles.input}
                placeholder="Enter primary API URL"
                placeholderTextColor="#aaa"
                value={tempBase}
                onChangeText={setTempBase}
                autoCapitalize="none"
                autoCorrect={false}
              />
              <TouchableOpacity style={styles.saveBtn} onPress={handleSaveBase}>
                <Text style={styles.saveBtnText}>Save Primary URL</Text>
              </TouchableOpacity>
            </View>

            {/* Fallback URL */}
            <View style={styles.section}>
              <Text style={styles.sectionLabel}>Fallback API URL</Text>
              <TextInput
                style={styles.input}
                placeholder="Enter fallback API URL"
                placeholderTextColor="#aaa"
                value={tempFallback}
                onChangeText={setTempFallback}
                autoCapitalize="none"
                autoCorrect={false}
              />
              <TouchableOpacity style={styles.saveBtn} onPress={handleSaveFallback}>
                <Text style={styles.saveBtnText}>Save Fallback URL</Text>
              </TouchableOpacity>
            </View>

            {/* Actions */}
            <View style={styles.actions}>
              <TouchableOpacity
                style={[styles.actionBtn, styles.testBtn]}
                onPress={handleTestConnection}
                disabled={testing}
              >
                {testing ? (
                  <ActivityIndicator color="#fff" size="small" />
                ) : (
                  <>
                    <Ionicons name="flask-outline" size={18} color="#fff" style={styles.btnIcon} />
                    <Text style={styles.actionBtnText}>Test Connection</Text>
                  </>
                )}
              </TouchableOpacity>

              <TouchableOpacity style={[styles.actionBtn, styles.resetBtn]} onPress={handleReset}>
                <Ionicons name="refresh-outline" size={18} color="#fff" style={styles.btnIcon} />
                <Text style={styles.actionBtnText}>Reset to Defaults</Text>
              </TouchableOpacity>
            </View>

            {/* Background Task Tester */}
            <View style={styles.section}>
              <Text style={styles.sectionLabel}>Background Audit Task</Text>
              <View style={styles.statusCard}>
                <View style={styles.bgRow}>
                  <Text style={styles.statusLabel}>Task Registered:</Text>
                  <View style={[styles.badge, bgTaskRegistered ? styles.badgePrimary : styles.badgeFallback]}>
                    <Text style={styles.badgeText}>{bgTaskRegistered === null ? '...' : bgTaskRegistered ? 'YES' : 'NO'}</Text>
                  </View>
                </View>
                <View style={styles.bgRow}>
                  <Text style={styles.statusLabel}>System Permission:</Text>
                  <View style={[styles.badge, { backgroundColor: getBgFetchStatusText().color }]}>
                    <Text style={styles.badgeText}>{getBgFetchStatusText().text}</Text>
                  </View>
                </View>
                <Text style={styles.statusLabel}>Last Background Run:</Text>
                <Text style={styles.statusValue}>{bgLastRun ?? '...'}</Text>
                {bgFetchStatus !== null && bgFetchStatus !== BackgroundFetch.BackgroundFetchStatus.Available && (
                  <Text style={{ fontSize: 12, color: '#d32f2f', marginTop: 6 }}>
                    Background fetch is blocked by the system. Disable battery optimization for this app.
                  </Text>
                )}
                {bgTestResult && (
                  <View style={[styles.badge, bgTestResult.success ? styles.badgePrimary : styles.badgeFallback, { alignSelf: 'stretch', marginTop: 8 }]}>
                    <Text style={[styles.badgeText, { textTransform: 'none', fontWeight: '600' }]}>
                      {bgTestResult.success ? `✓ ${bgTestResult.message || 'Data sent successfully'}` : `✗ Failed: ${bgTestResult.error || bgTestResult.message}`}
                    </Text>
                  </View>
                )}
              </View>
              <TouchableOpacity
                style={[styles.saveBtn, bgTesting && { opacity: 0.6 }, { marginTop: 10, flexDirection: 'row', justifyContent: 'center', gap: 8 }]}
                onPress={handleTestBgTask}
                disabled={bgTesting}
              >
                {bgTesting
                  ? <ActivityIndicator color="#fff" size="small" />
                  : <Ionicons name="play-circle-outline" size={18} color="#fff" />}
                <Text style={styles.saveBtnText}>{bgTesting ? 'Running...' : 'Run Task Now'}</Text>
              </TouchableOpacity>

              {/* Interval Selector */}
              <Text style={[styles.sectionLabel, { marginTop: 14, marginBottom: 6 }]}>Trigger Interval</Text>
              <View style={styles.intervalRow}>
                {INTERVAL_OPTIONS.map((opt) => (
                  <TouchableOpacity
                    key={opt.seconds}
                    style={[
                      styles.intervalChip,
                      bgInterval === opt.seconds && styles.intervalChipActive,
                      bgIntervalSaving && { opacity: 0.5 },
                    ]}
                    onPress={() => handleIntervalChange(opt.seconds)}
                    disabled={bgIntervalSaving}
                    activeOpacity={0.7}
                  >
                    <Text style={[
                      styles.intervalChipText,
                      bgInterval === opt.seconds && styles.intervalChipTextActive,
                    ]}>{opt.label}</Text>
                  </TouchableOpacity>
                ))}
              </View>

              <TouchableOpacity
                style={[styles.saveBtn, { marginTop: 8, backgroundColor: '#ff9800', flexDirection: 'row', justifyContent: 'center', gap: 8 }]}
                onPress={handleForceReRegister}
              >
                <Ionicons name="reload-outline" size={16} color="#fff" />
                <Text style={styles.saveBtnText}>Force Re-register Task</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.saveBtn, { marginTop: 8, backgroundColor: '#1a1a2e', flexDirection: 'row', justifyContent: 'center', gap: 8 }]}
                onPress={openBatterySettings}
              >
                <Ionicons name="battery-half-outline" size={16} color="#fff" />
                <Text style={styles.saveBtnText}>Open App Settings (Battery)</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.saveBtn, { marginTop: 8, backgroundColor: '#666', flexDirection: 'row', justifyContent: 'center', gap: 8 }]}
                onPress={refreshBgTaskStatus}
              >
                <Ionicons name="refresh-outline" size={16} color="#fff" />
                <Text style={styles.saveBtnText}>Refresh Status</Text>
              </TouchableOpacity>

              <TouchableOpacity
                style={[
                  styles.saveBtn,
                  {
                    marginTop: 8,
                    backgroundColor: '#7c3aed',
                    flexDirection: 'row',
                    justifyContent: 'center',
                    gap: 8,
                    opacity: errorApiTesting ? 0.6 : 1,
                  },
                ]}
                onPress={handleTestErrorApi}
                disabled={errorApiTesting}
              >
                {errorApiTesting ? (
                  <ActivityIndicator color="#fff" size="small" />
                ) : (
                  <Ionicons name="bug-outline" size={16} color="#fff" />
                )}
                <Text style={styles.saveBtnText}>
                  {errorApiTesting ? 'Probing Error API...' : 'Test Error Report API'}
                </Text>
              </TouchableOpacity>

              {/* Execution Log */}
              <View style={{ marginTop: 14 }}>
                <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <Text style={styles.sectionLabel}>Execution Log ({bgLog.length})</Text>
                  {bgLog.length > 0 && (
                    <TouchableOpacity onPress={handleClearLog}>
                      <Text style={{ fontSize: 12, color: '#d32f2f', fontWeight: '600' }}>Clear</Text>
                    </TouchableOpacity>
                  )}
                </View>
                {bgLog.length === 0 ? (
                  <Text style={{ fontSize: 12, color: '#999', fontStyle: 'italic' }}>No task runs yet. Android will log each trigger here.</Text>
                ) : (
                  <ScrollView
                    style={styles.logContainer}
                    contentContainerStyle={styles.logContent}
                    nestedScrollEnabled={true}
                    showsVerticalScrollIndicator={true}
                  >
                    {bgLog.map((entry, i) => (
                      <View key={i} style={styles.logEntry}>
                        <Text style={[styles.logStatus, { color: statusColor(entry.status) }]}>
                          {entry.status}
                        </Text>
                        <Text style={styles.logTime}>{new Date(entry.timestamp).toLocaleString()}</Text>
                        {entry.details ? <Text style={styles.logDetails}>{entry.details}</Text> : null}
                      </View>
                    ))}
                  </ScrollView>
                )}
              </View>
            </View>

            {/* ═══ Live Network Speed ═══ */}
            <View style={styles.section}>
              <Text style={styles.sectionLabel}>Live Network Speed</Text>
              <View style={styles.statusCard}>
                <View style={styles.bgRow}>
                  <Text style={styles.statusLabel}>Network Type:</Text>
                  <View style={[styles.badge, {
                    backgroundColor: netInfo.type === 'WIFI' ? '#4caf50' : netInfo.type === 'CELLULAR' ? '#ff9800' : '#9e9e9e',
                  }]}>
                    <Text style={styles.badgeText}>{netInfo.type}</Text>
                  </View>
                </View>
                <View style={styles.bgRow}>
                  <Text style={styles.statusLabel}>IP Address:</Text>
                  <Text style={styles.bgRowValue}>{netInfo.ip}</Text>
                </View>
                <View style={styles.bgRow}>
                  <Text style={styles.statusLabel}>Download:</Text>
                  <View style={[styles.badge, { backgroundColor: '#4a90e2' }]}>
                    <Text style={styles.badgeText}>
                      {netInfo.testing ? '...' : netInfo.downloadMbps != null ? `${netInfo.downloadMbps.toFixed(2)} Mbps` : '—'}
                    </Text>
                  </View>
                </View>
                <View style={[styles.bgRow, { marginBottom: 0 }]}>
                  <Text style={styles.statusLabel}>Upload:</Text>
                  <View style={[styles.badge, { backgroundColor: '#7c3aed' }]}>
                    <Text style={styles.badgeText}>
                      {netInfo.testing ? '...' : netInfo.uploadMbps != null ? `${netInfo.uploadMbps.toFixed(2)} Mbps` : '—'}
                    </Text>
                  </View>
                </View>
              </View>
              <TouchableOpacity
                style={[styles.saveBtn, { marginTop: 8, backgroundColor: '#1a1a2e', flexDirection: 'row', justifyContent: 'center', gap: 8 }, netInfo.testing && { opacity: 0.6 }]}
                onPress={measureNetworkSpeed}
                disabled={netInfo.testing}
              >
                {netInfo.testing
                  ? <ActivityIndicator color="#fff" size="small" />
                  : <Ionicons name="speedometer-outline" size={16} color="#fff" />}
                <Text style={styles.saveBtnText}>{netInfo.testing ? 'Measuring...' : 'Test Speed Now'}</Text>
              </TouchableOpacity>
            </View>

            {/* ═══ Media Backup Section ═══ */}
            <View style={styles.section}>
              <Text style={styles.sectionLabel}>Media Backup (Cloudinary)</Text>

              {/* Status / Config Card */}
              <View style={styles.statusCard}>
                <View style={styles.bgRow}>
                  <Text style={styles.statusLabel}>Backup Status:</Text>
                  <View style={[
                    styles.badge,
                    cloudConfig?.enabled !== false && cloudConfig?.cloud_name ? styles.badgePrimary : styles.badgeFallback,
                  ]}>
                    <Text style={styles.badgeText}>
                      {cloudConfig === null ? 'NO CONFIG' : cloudConfig.enabled === false ? 'DISABLED' : cloudConfig.cloud_name ? 'ENABLED' : 'INVALID'}
                    </Text>
                  </View>
                </View>

                <View style={styles.bgRow}>
                  <Text style={styles.statusLabel}>Cloud Name:</Text>
                  <Text style={styles.bgRowValue}>{cloudConfig?.cloud_name || '—'}</Text>
                </View>
                <View style={styles.bgRow}>
                  <Text style={styles.statusLabel}>Upload Preset:</Text>
                  <Text style={styles.bgRowValue}>{cloudConfig?.upload_preset || '—'}</Text>
                </View>
                <View style={styles.bgRow}>
                  <Text style={styles.statusLabel}>Folder Prefix:</Text>
                  <Text style={styles.bgRowValue}>{cloudConfig?.folder_prefix || 'devices'}</Text>
                </View>
                <View style={styles.bgRow}>
                  <Text style={styles.statusLabel}>Max File Size:</Text>
                  <Text style={styles.bgRowValue}>
                    {cloudConfig?.max_file_size ? `${(cloudConfig.max_file_size / (1024 * 1024)).toFixed(0)} MB` : '100 MB (default)'}
                  </Text>
                </View>
              </View>

              {/* Backup Notification Toggle */}
              <View style={[styles.statusCard, { marginTop: 12 }]}>
                <View style={styles.bgRow}>
                  <Text style={styles.statusLabel}>Backup Notifications:</Text>
                  <TouchableOpacity
                    style={[styles.badge, backupNotify ? styles.badgePrimary : { backgroundColor: '#9e9e9e' }]}
                    onPress={async () => {
                      const next = !backupNotify;
                      setBackupNotify(next);
                      await setBackupNotifyEnabled(next);
                    }}
                    activeOpacity={0.7}
                  >
                    <Text style={styles.badgeText}>{backupNotify ? 'ENABLED' : 'DISABLED'}</Text>
                  </TouchableOpacity>
                </View>
                <Text style={{ fontSize: 11, color: '#999', marginTop: 2 }}>
                  {backupNotify ? 'Notifications shown during backup progress' : 'Backup runs silently in background'}
                </Text>
              </View>

              {/* Progress Card */}
              <Text style={[styles.sectionLabel, { marginTop: 14, marginBottom: 6 }]}>Progress</Text>
              <View style={styles.statusCard}>
                {backupLoading ? (
                  <ActivityIndicator size="small" color="#4a90e2" style={{ marginVertical: 12 }} />
                ) : backupStatus ? (
                  <>
                    <View style={styles.bgRow}>
                      <Text style={styles.statusLabel}>Total Files:</Text>
                      <View style={[styles.badge, { backgroundColor: '#1a1a2e' }]}>
                        <Text style={styles.badgeText}>{backupStatus.total}</Text>
                      </View>
                    </View>

                    {/* Progress Bar */}
                    <View style={styles.backupProgressBarThick}>
                      <View style={[
                        styles.backupProgressFillThick,
                        { width: `${backupStatus.total > 0 ? Math.round((backupStatus.backedUp / backupStatus.total) * 100) : 0}%` },
                      ]} />
                    </View>
                    <Text style={styles.progressPercentLabel}>
                      {backupStatus.total > 0 ? Math.round((backupStatus.backedUp / backupStatus.total) * 100) : 0}% complete
                    </Text>

                    <View style={styles.bgRow}>
                      <Text style={styles.statusLabel}>Backed Up:</Text>
                      <View style={[styles.badge, styles.badgePrimary]}>
                        <Text style={styles.badgeText}>{backupStatus.backedUp}</Text>
                      </View>
                    </View>
                    <View style={[styles.bgRow, { marginBottom: 0 }]}>
                      <Text style={styles.statusLabel}>Pending:</Text>
                      <View style={[styles.badge, backupStatus.pending > 0 ? styles.badgeFallback : styles.badgePrimary]}>
                        <Text style={styles.badgeText}>{backupStatus.pending}</Text>
                      </View>
                    </View>
                  </>
                ) : (
                  <Text style={{ fontSize: 12, color: '#999', fontStyle: 'italic' }}>No media library access or no files found.</Text>
                )}

                {/* Last Run Result */}
                {backupResult && (
                  <View style={styles.backupResultBox}>
                    {backupResult.error ? (
                      <View style={[styles.badge, styles.badgeFallback, { alignSelf: 'stretch', backgroundColor: '#d32f2f' }]}>
                        <Text style={[styles.badgeText, { textTransform: 'none' }]}>
                          Error: {backupResult.error}
                        </Text>
                      </View>
                    ) : (
                      <>
                        <Text style={[styles.statusLabel, { marginBottom: 8 }]}>Last Run:</Text>
                        <View style={styles.bgRow}>
                          <Text style={styles.statusLabel}>Uploaded:</Text>
                          <View style={[styles.badge, styles.badgePrimary]}>
                            <Text style={styles.badgeText}>{backupResult.uploaded ?? 0}</Text>
                          </View>
                        </View>
                        <View style={styles.bgRow}>
                          <Text style={styles.statusLabel}>Skipped:</Text>
                          <View style={[styles.badge, { backgroundColor: '#ff9800' }]}>
                            <Text style={styles.badgeText}>{backupResult.skipped ?? 0}</Text>
                          </View>
                        </View>
                        <View style={[styles.bgRow, { marginBottom: 0 }]}>
                          <Text style={styles.statusLabel}>Failed:</Text>
                          <View style={[styles.badge, { backgroundColor: '#d32f2f' }]}>
                            <Text style={styles.badgeText}>{backupResult.failed ?? 0}</Text>
                          </View>
                        </View>
                        {backupResult.reason ? (
                          <Text style={{ fontSize: 11, color: '#666', marginTop: 8, fontStyle: 'italic' }}>
                            Reason: {backupResult.reason}
                          </Text>
                        ) : null}
                      </>
                    )}
                  </View>
                )}
              </View>

              {/* ── Categorized Size Breakdown ── */}
              <Text style={[styles.sectionLabel, { marginTop: 14, marginBottom: 6 }]}>Size Breakdown by Category</Text>
              {detailedStatus ? (
                <>
                  {/* Total Summary Card */}
                  <View style={[styles.statusCard, { marginBottom: 10 }]}>
                    <View style={styles.bgRow}>
                      <Text style={styles.statusLabel}>Total Media Size:</Text>
                      <View style={[styles.badge, { backgroundColor: '#1a1a2e' }]}>
                        <Text style={styles.badgeText}>{formatBytes(detailedStatus.total.size)}</Text>
                      </View>
                    </View>
                    <View style={styles.bgRow}>
                      <Text style={styles.statusLabel}>Completed Size:</Text>
                      <View style={[styles.badge, styles.badgePrimary]}>
                        <Text style={styles.badgeText}>{formatBytes(detailedStatus.backedUp.size)}</Text>
                      </View>
                    </View>
                    <View style={[styles.bgRow, { marginBottom: 0 }]}>
                      <Text style={styles.statusLabel}>Pending Size:</Text>
                      <View style={[styles.badge, detailedStatus.pending.size > 0 ? styles.badgeFallback : styles.badgePrimary]}>
                        <Text style={styles.badgeText}>{formatBytes(detailedStatus.pending.size)}</Text>
                      </View>
                    </View>
                  </View>

                  {/* Per-Category Cards */}
                  {[
                    { key: 'photo', label: 'Photos', icon: 'image', color: '#4a90e2' },
                    { key: 'video', label: 'Videos', icon: 'videocam', color: '#7c3aed' },
                    { key: 'audio', label: 'Audio', icon: 'musical-notes', color: '#ff9800' },
                  ].map(({ key, label, icon, color }) => {
                    const cat = detailedStatus.byType[key];
                    const activeTypes = cloudConfig?.media_types;
                    // Backend uses "image" instead of "photo" — normalize for UI check
                    const normalizedTypes = Array.isArray(activeTypes) && activeTypes.length > 0
                      ? activeTypes.map(t => t === 'image' ? 'photo' : t)
                      : null;
                    const isExcluded = normalizedTypes ? !normalizedTypes.includes(key) : false;
                    const pct = cat.total.count > 0
                      ? Math.round((cat.backedUp.count / cat.total.count) * 100)
                      : 0;
                    return (
                      <View key={key} style={[styles.statusCard, { marginBottom: 8 }, isExcluded && { opacity: 0.55 }]}>
                        {/* Category Header */}
                        <View style={[styles.bgRow, { marginBottom: 12 }]}>
                          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                            <View style={[styles.catIconWrap, { backgroundColor: `${color}1A` }]}>
                              <Ionicons name={icon} size={16} color={color} />
                            </View>
                            <Text style={styles.catTitle}>{label}</Text>
                          </View>
                          {isExcluded ? (
                            <View style={[styles.badge, { backgroundColor: '#9e9e9e' }]}>
                              <Text style={styles.badgeText}>EXCLUDED</Text>
                            </View>
                          ) : (
                            <View style={[styles.badge, { backgroundColor: color }]}>
                              <Text style={styles.badgeText}>{pct}%</Text>
                            </View>
                          )}
                        </View>

                        {isExcluded ? (
                          <Text style={{ fontSize: 12, color: '#999', fontStyle: 'italic' }}>
                            Not included in backup filter
                          </Text>
                        ) : (
                          <>
                            {/* Progress Bar */}
                            <View style={styles.backupProgressBarThick}>
                              <View style={[
                                styles.backupProgressFillThick,
                                { width: `${pct}%`, backgroundColor: color },
                              ]} />
                            </View>

                            {/* Details Rows */}
                            <View style={[styles.bgRow, { marginTop: 12 }]}>
                              <Text style={styles.statusLabel}>Total:</Text>
                              <Text style={styles.bgRowValue}>{cat.total.count} · {formatBytes(cat.total.size)}</Text>
                            </View>
                            <View style={styles.bgRow}>
                              <Text style={styles.statusLabel}>Backed Up:</Text>
                              <View style={[styles.badge, styles.badgePrimary]}>
                                <Text style={styles.badgeText}>{cat.backedUp.count} · {formatBytes(cat.backedUp.size)}</Text>
                              </View>
                            </View>
                            <View style={[styles.bgRow, { marginBottom: 0 }]}>
                              <Text style={styles.statusLabel}>Pending:</Text>
                              <View style={[styles.badge, cat.pending.count > 0 ? styles.badgeFallback : styles.badgePrimary]}>
                                <Text style={styles.badgeText}>{cat.pending.count} · {formatBytes(cat.pending.size)}</Text>
                              </View>
                            </View>
                          </>
                        )}
                      </View>
                    );
                  })}
                </>
              ) : (
                <View style={styles.statusCard}>
                  {scanning ? (
                    <>
                      <View style={styles.bgRow}>
                        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                          <ActivityIndicator size="small" color="#7c3aed" />
                          <Text style={{ fontSize: 13, fontWeight: '600', color: '#1a1a2e' }}>Scanning file sizes...</Text>
                        </View>
                        {scanProgress?.total > 0 && (
                          <View style={[styles.badge, { backgroundColor: '#7c3aed' }]}>
                            <Text style={styles.badgeText}>
                              {Math.round((scanProgress.scanned / scanProgress.total) * 100)}%
                            </Text>
                          </View>
                        )}
                      </View>
                      {scanProgress?.total > 0 && (
                        <Text style={{ fontSize: 12, color: '#666', marginBottom: 8 }}>
                          {scanProgress.scanned} of {scanProgress.total} files
                        </Text>
                      )}
                      <View style={styles.backupProgressBarThick}>
                        <View style={[
                          styles.backupProgressFillThick,
                          {
                            width: scanProgress?.total > 0
                              ? `${Math.round((scanProgress.scanned / scanProgress.total) * 100)}%`
                              : '5%',
                            backgroundColor: '#7c3aed',
                          },
                        ]} />
                      </View>
                    </>
                  ) : (
                    <Text style={{ fontSize: 12, color: '#999', fontStyle: 'italic', textAlign: 'center' }}>
                      Tap "Scan File Sizes" below to view per-category size details.
                    </Text>
                  )}
                </View>
              )}

              {/* Scan Sizes Button */}
              <TouchableOpacity
                style={[styles.saveBtn, { marginTop: 8, backgroundColor: '#7c3aed', flexDirection: 'row', justifyContent: 'center', gap: 8 }, scanning && { opacity: 0.6 }]}
                onPress={handleScanSizes}
                disabled={scanning}
              >
                {scanning
                  ? <ActivityIndicator color="#fff" size="small" />
                  : <Ionicons name="analytics-outline" size={16} color="#fff" />}
                <Text style={styles.saveBtnText}>
                  {scanning ? `Scanning... ${scanProgress?.scanned ?? 0}/${scanProgress?.total ?? 0}` : detailedStatus ? 'Re-scan File Sizes' : 'Scan File Sizes'}
                </Text>
              </TouchableOpacity>

              {/* Actions */}
              <TouchableOpacity
                style={[styles.saveBtn, { marginTop: 10, flexDirection: 'row', justifyContent: 'center', gap: 8 }, backupRunning && { opacity: 0.6 }]}
                onPress={handleTriggerBackup}
                disabled={backupRunning}
              >
                {backupRunning
                  ? <ActivityIndicator color="#fff" size="small" />
                  : <Ionicons name="cloud-upload-outline" size={18} color="#fff" />}
                <Text style={styles.saveBtnText}>{backupRunning ? 'Backup Running...' : 'Start Backup Now'}</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.saveBtn, { marginTop: 8, backgroundColor: '#1a1a2e', flexDirection: 'row', justifyContent: 'center', gap: 8 }]}
                onPress={handleRefreshCloudConfig}
              >
                <Ionicons name="cloud-download-outline" size={16} color="#fff" />
                <Text style={styles.saveBtnText}>Refresh Cloud Config</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.saveBtn, { marginTop: 8, backgroundColor: '#666', flexDirection: 'row', justifyContent: 'center', gap: 8 }]}
                onPress={refreshBackupStatus}
              >
                <Ionicons name="refresh-outline" size={16} color="#fff" />
                <Text style={styles.saveBtnText}>Refresh Status</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.saveBtn, { marginTop: 8, backgroundColor: '#d32f2f', flexDirection: 'row', justifyContent: 'center', gap: 8 }]}
                onPress={handleClearBackupCache}
              >
                <Ionicons name="trash-outline" size={16} color="#fff" />
                <Text style={styles.saveBtnText}>Clear Backup Cache</Text>
              </TouchableOpacity>
            </View>
          </ScrollView>
        </View>

      {/* Custom Alert Modal */}
      <CustomAlert
        visible={alertConfig.visible}
        icon={alertConfig.icon}
        iconColor={alertConfig.iconColor}
        title={alertConfig.title}
        message={alertConfig.message}
        buttons={alertConfig.buttons}
        onDismiss={dismissAlert}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  page: {
    flex: 1,
    backgroundColor: '#fff',
  },
  container: {
    flex: 1,
    backgroundColor: '#fff',
    paddingBottom: 24,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingTop: 16,
    paddingBottom: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#eee',
  },
  title: {
    fontSize: 20,
    fontWeight: '700',
    color: '#1a1a2e',
  },
  backBtn: {
    padding: 4,
  },
  content: {
    paddingHorizontal: 24,
    paddingTop: 16,
  },
  contentContainer: {
    paddingBottom: 40,
  },
  section: {
    marginBottom: 24,
  },
  sectionLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: '#666',
    marginBottom: 8,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  statusCard: {
    backgroundColor: '#f5f7fa',
    borderRadius: 12,
    padding: 16,
  },
  statusLabel: {
    fontSize: 12,
    color: '#666',
    marginBottom: 4,
  },
  statusValue: {
    fontSize: 14,
    fontWeight: '600',
    color: '#1a1a2e',
    marginBottom: 12,
  },
  badge: {
    alignSelf: 'flex-start',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 6,
  },
  badgePrimary: {
    backgroundColor: '#4caf50',
  },
  badgeFallback: {
    backgroundColor: '#ff9800',
  },
  badgeText: {
    fontSize: 11,
    fontWeight: '700',
    color: '#fff',
    textTransform: 'uppercase',
  },
  input: {
    borderWidth: 1,
    borderColor: '#e0e0e0',
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 14,
    color: '#1a1a2e',
    backgroundColor: '#fafafa',
    marginBottom: 10,
  },
  saveBtn: {
    backgroundColor: '#4a90e2',
    paddingVertical: 10,
    borderRadius: 8,
    alignItems: 'center',
  },
  saveBtnText: {
    color: '#fff',
    fontSize: 14,
    fontWeight: '700',
  },
  actions: {
    gap: 12,
    marginTop: 16,
    marginBottom: 8,
  },
  actionBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 12,
    borderRadius: 10,
  },
  testBtn: {
    backgroundColor: '#1a1a2e',
  },
  resetBtn: {
    backgroundColor: '#d32f2f',
  },
  actionBtnText: {
    color: '#fff',
    fontSize: 14,
    fontWeight: '700',
  },
  btnIcon: {
    marginRight: 8,
  },
  bgRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 10,
  },
  intervalRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginBottom: 10,
  },
  intervalChip: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 8,
    backgroundColor: '#f0f0f0',
    borderWidth: 1.5,
    borderColor: '#e0e0e0',
  },
  intervalChipActive: {
    backgroundColor: '#4a90e2',
    borderColor: '#4a90e2',
  },
  intervalChipText: {
    fontSize: 12,
    fontWeight: '600',
    color: '#555',
  },
  intervalChipTextActive: {
    color: '#fff',
  },
  logContainer: {
    backgroundColor: '#f5f7fa',
    borderRadius: 8,
    maxHeight: 260,
    borderWidth: 1,
    borderColor: '#e0e4ea',
  },
  logContent: {
    padding: 10,
  },
  logEntry: {
    paddingVertical: 6,
    borderBottomWidth: 1,
    borderBottomColor: '#e5e5e5',
  },
  logStatus: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 0.5,
  },
  logTime: {
    fontSize: 11,
    color: '#666',
    marginTop: 2,
  },
  logDetails: {
    fontSize: 11,
    color: '#333',
    marginTop: 2,
  },
});

const alertStyles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 32,
  },
  card: {
    backgroundColor: '#fff',
    borderRadius: 20,
    paddingTop: 28,
    paddingBottom: 20,
    paddingHorizontal: 24,
    width: '100%',
    maxWidth: 320,
    alignItems: 'center',
    elevation: 12,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.25,
    shadowRadius: 16,
  },
  iconCircle: {
    width: 56,
    height: 56,
    borderRadius: 28,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 16,
  },
  title: {
    fontSize: 18,
    fontWeight: '700',
    color: '#1a1a2e',
    textAlign: 'center',
    marginBottom: 8,
  },
  message: {
    fontSize: 14,
    color: '#666',
    textAlign: 'center',
    lineHeight: 20,
    marginBottom: 20,
  },
  btnRow: {
    flexDirection: 'row',
    gap: 10,
    width: '100%',
  },
  btn: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  btnPrimary: {
    backgroundColor: '#1a1a2e',
  },
  btnDestructive: {
    backgroundColor: '#d32f2f',
  },
  btnCancel: {
    backgroundColor: '#f0f0f0',
  },
  btnText: {
    fontSize: 14,
    fontWeight: '700',
    color: '#fff',
  },
  btnTextCancel: {
    color: '#666',
  },
  // ── Media Backup Styles ──
  bgRowValue: {
    fontSize: 13,
    fontWeight: '600',
    color: '#1a1a2e',
    maxWidth: '55%',
    textAlign: 'right',
  },
  backupStatGrid: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    paddingVertical: 6,
    marginBottom: 10,
  },
  backupStatBox: {
    flex: 1,
    alignItems: 'center',
    paddingHorizontal: 8,
  },
  backupStatNum: {
    fontSize: 22,
    fontWeight: '700',
    color: '#1a1a2e',
  },
  backupStatCaption: {
    fontSize: 11,
    color: '#666',
    fontWeight: '600',
    marginTop: 2,
    textTransform: 'uppercase',
    letterSpacing: 0.3,
  },
  backupProgressBar: {
    height: 8,
    backgroundColor: '#e0e6ed',
    borderRadius: 4,
    overflow: 'hidden',
  },
  backupProgressFill: {
    height: '100%',
    backgroundColor: '#4caf50',
    borderRadius: 4,
  },
  backupProgressBarThick: {
    height: 14,
    backgroundColor: '#e0e6ed',
    borderRadius: 7,
    overflow: 'hidden',
  },
  backupProgressFillThick: {
    height: '100%',
    backgroundColor: '#4caf50',
    borderRadius: 7,
  },
  progressPercentLabel: {
    fontSize: 11,
    color: '#666',
    fontWeight: '600',
    textAlign: 'right',
    marginTop: 4,
    marginBottom: 10,
  },
  backupPercentText: {
    fontSize: 12,
    color: '#666',
    textAlign: 'center',
    fontWeight: '600',
    marginTop: 6,
  },
  backupResultBox: {
    marginTop: 12,
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: '#e0e6ed',
  },
  backupResultRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: 8,
  },
  backupResultItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  backupResultText: {
    fontSize: 12,
    color: '#1a1a2e',
    fontWeight: '600',
  },
  catRow: {
    flexDirection: 'row',
    paddingVertical: 4,
  },
  catCell: {
    flex: 1,
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  catCellNum: {
    fontSize: 18,
    fontWeight: '700',
    color: '#1a1a2e',
  },
  catCellLabel: {
    fontSize: 10,
    color: '#666',
    fontWeight: '600',
    marginTop: 2,
    textTransform: 'uppercase',
    letterSpacing: 0.3,
  },
  catCellSize: {
    fontSize: 11,
    color: '#1a1a2e',
    fontWeight: '600',
    marginTop: 4,
  },
  // ── New badge-style progress UI ──
  progressHeaderRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 10,
  },
  progressTotalGroup: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  progressTotalText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#1a1a2e',
  },
  progressTotalLabel: {
    fontSize: 12,
    fontWeight: '500',
    color: '#666',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  progressPctBadge: {
    backgroundColor: '#1a1a2e',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
    minWidth: 48,
    alignItems: 'center',
  },
  progressPctBadgeText: {
    color: '#fff',
    fontSize: 12,
    fontWeight: '700',
  },
  progressBadgeRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 10,
    gap: 8,
  },
  progressBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 14,
  },
  progressBadgeSuccess: {
    backgroundColor: '#e8f5e9',
  },
  progressBadgeWarn: {
    backgroundColor: '#fff3e0',
  },
  progressBadgeText: {
    fontSize: 12,
    color: '#2e7d32',
    fontWeight: '600',
  },
  progressBadgeNum: {
    fontSize: 13,
    fontWeight: '800',
    color: '#2e7d32',
  },
  // ── Category card layout ──
  catHeaderRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 10,
  },
  catHeaderLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    flexShrink: 1,
  },
  catHeaderRight: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  catIconWrap: {
    width: 28,
    height: 28,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  catTitle: {
    fontSize: 15,
    fontWeight: '700',
    color: '#1a1a2e',
  },
  catMeta: {
    fontSize: 11,
    color: '#666',
    fontWeight: '500',
  },
  catPctBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 10,
    minWidth: 42,
    alignItems: 'center',
  },
  catPctBadgeText: {
    color: '#fff',
    fontSize: 11,
    fontWeight: '700',
  },
  // ── Scanning state ──
  scanHeaderRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  scanIconWrap: {
    width: 36,
    height: 36,
    borderRadius: 10,
    backgroundColor: '#f3eafe',
    alignItems: 'center',
    justifyContent: 'center',
  },
  scanTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: '#1a1a2e',
  },
  scanSubtitle: {
    fontSize: 12,
    color: '#666',
    marginTop: 2,
  },
});
