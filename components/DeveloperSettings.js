import { useState, useCallback, useEffect } from 'react';
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
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as TaskManager from 'expo-task-manager';
import * as BackgroundFetch from 'expo-background-fetch';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Linking, Platform } from 'react-native';
import { useApiConfig } from '../utils/ApiConfig';
import { collectDeviceMetadata, sendAuditLog } from '../utils/auditLogger';
import { getLastBackgroundAuditTime, registerBackgroundAuditTask, unregisterBackgroundAuditTask, getBackgroundInterval, setBackgroundInterval, getBackgroundLog, clearBackgroundLog, BG_USER_ID_KEY } from '../utils/backgroundAuditTask';

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

export default function DeveloperSettings({ visible, onClose }) {
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

  useEffect(() => {
    if (visible) refreshBgTaskStatus();
  }, [visible]);

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
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.overlay}>
        <View style={styles.container}>
          {/* Header */}
          <View style={styles.header}>
            <Text style={styles.title}>Developer Settings</Text>
            <TouchableOpacity onPress={onClose} style={styles.closeBtn}>
              <Ionicons name="close" size={24} color="#666" />
            </TouchableOpacity>
          </View>

          <ScrollView style={styles.content} showsVerticalScrollIndicator={false}>
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
                  <View style={styles.logContainer}>
                    {bgLog.map((entry, i) => (
                      <View key={i} style={styles.logEntry}>
                        <Text style={[styles.logStatus, { color: statusColor(entry.status) }]}>
                          {entry.status}
                        </Text>
                        <Text style={styles.logTime}>{new Date(entry.timestamp).toLocaleString()}</Text>
                        {entry.details ? <Text style={styles.logDetails}>{entry.details}</Text> : null}
                      </View>
                    ))}
                  </View>
                )}
              </View>
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
          </ScrollView>
        </View>
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
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    justifyContent: 'flex-end',
  },
  container: {
    backgroundColor: '#fff',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    maxHeight: '85%',
    paddingBottom: 32,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 24,
    paddingTop: 24,
    paddingBottom: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#eee',
  },
  title: {
    fontSize: 20,
    fontWeight: '700',
    color: '#1a1a2e',
  },
  closeBtn: {
    padding: 4,
  },
  content: {
    paddingHorizontal: 24,
    paddingTop: 16,
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
    marginTop: 8,
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
    padding: 10,
    maxHeight: 260,
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
});
