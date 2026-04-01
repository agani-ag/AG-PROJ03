import { useState, useCallback } from 'react';
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
import { useApiConfig } from '../utils/ApiConfig';

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
