import { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  Modal,
  Alert,
  ScrollView,
  ActivityIndicator,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useApiConfig } from '../utils/ApiConfig';

export default function DeveloperSettings({ visible, onClose }) {
  const { apiBase, fallbackUrl, currentUrl, isUsingFallback, updateApiBase, updateFallback, checkHealth, resetToDefaults } = useApiConfig();

  const [tempBase, setTempBase] = useState(apiBase);
  const [tempFallback, setTempFallback] = useState(fallbackUrl);
  const [testing, setTesting] = useState(false);

  const handleSaveBase = async () => {
    if (!tempBase.trim()) {
      Alert.alert('Error', 'API Base URL cannot be empty');
      return;
    }
    await updateApiBase(tempBase);
    Alert.alert('Saved', 'API Base URL updated successfully');
  };

  const handleSaveFallback = async () => {
    if (!tempFallback.trim()) {
      Alert.alert('Error', 'Fallback URL cannot be empty');
      return;
    }
    await updateFallback(tempFallback);
    Alert.alert('Saved', 'Fallback URL updated successfully');
  };

  const handleTestConnection = async () => {
    setTesting(true);
    const workingUrl = await checkHealth();
    setTesting(false);

    Alert.alert(
      'Connection Test',
      `Working URL: ${workingUrl}\n\nUsing ${isUsingFallback ? 'Fallback' : 'Primary'} endpoint`,
      [{ text: 'OK' }]
    );
  };

  const handleReset = () => {
    Alert.alert(
      'Reset to Defaults',
      'This will reset all API URLs to default values. Continue?',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Reset',
          style: 'destructive',
          onPress: async () => {
            await resetToDefaults();
            setTempBase(apiBase);
            setTempFallback(fallbackUrl);
            Alert.alert('Reset', 'API URLs reset to defaults');
          },
        },
      ]
    );
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
