import { Modal, View, Text, TouchableOpacity, StyleSheet, Animated, ActivityIndicator } from 'react-native';
import { useEffect, useRef, useState } from 'react';
import { Ionicons } from '@expo/vector-icons';
import { useAppName } from '../utils/AppContext';

export default function LogoutConfirmation({ visible, onCancel, onConfirm }) {
  const { appName } = useAppName();
  const scaleAnim = useRef(new Animated.Value(0)).current;
  const fadeAnim = useRef(new Animated.Value(0)).current;
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    if (visible) {
      // Reset loading state every time the modal opens
      setLoggingOut(false);
      // Animate in
      Animated.parallel([
        Animated.spring(scaleAnim, {
          toValue: 1,
          tension: 50,
          friction: 7,
          useNativeDriver: true,
        }),
        Animated.timing(fadeAnim, {
          toValue: 1,
          duration: 200,
          useNativeDriver: true,
        }),
      ]).start();
    } else {
      // Reset animation
      scaleAnim.setValue(0);
      fadeAnim.setValue(0);
    }
  }, [visible]);

  const handleConfirm = async () => {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await onConfirm?.();
    } catch (err) {
      // Surface failure by re-enabling the button so user can retry
      console.warn('[Logout] confirm error:', err?.message);
      setLoggingOut(false);
    }
  };

  const handleCancel = () => {
    if (loggingOut) return;
    onCancel?.();
  };

  if (!visible) return null;

  return (
    <Modal visible={visible} transparent animationType="none" onRequestClose={handleCancel}>
      <Animated.View style={[styles.overlay, { opacity: fadeAnim }]}>
        <TouchableOpacity
          style={styles.backdrop}
          activeOpacity={1}
          onPress={handleCancel}
        />

        <Animated.View style={[styles.dialog, { transform: [{ scale: scaleAnim }] }]}>
          {/* Icon */}
          <View style={styles.iconContainer}>
            <View style={styles.iconCircle}>
              <Ionicons name="log-out-outline" size={40} color="#d32f2f" />
            </View>
          </View>

          {/* Title */}
          <Text style={styles.title}>{loggingOut ? 'Logging out...' : 'Logout'}</Text>

          {/* Message */}
          <Text style={styles.message}>
            {loggingOut
              ? 'Please wait while we sign you out securely.'
              : `Are you sure you want to logout from ${appName}?`}
          </Text>

          {/* Buttons */}
          <View style={styles.buttons}>
            <TouchableOpacity
              style={[styles.button, styles.cancelButton, loggingOut && styles.buttonDisabled]}
              onPress={handleCancel}
              activeOpacity={0.8}
              disabled={loggingOut}
            >
              <Text style={styles.cancelButtonText}>Cancel</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={[styles.button, styles.logoutButton, loggingOut && styles.buttonLoading]}
              onPress={handleConfirm}
              activeOpacity={0.8}
              disabled={loggingOut}
            >
              {loggingOut ? (
                <ActivityIndicator color="#fff" size="small" />
              ) : (
                <>
                  <Ionicons name="log-out-outline" size={18} color="#fff" style={styles.buttonIcon} />
                  <Text style={styles.logoutButtonText}>Logout</Text>
                </>
              )}
            </TouchableOpacity>
          </View>
        </Animated.View>
      </Animated.View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.6)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
  },
  backdrop: {
    ...StyleSheet.absoluteFillObject,
  },
  dialog: {
    backgroundColor: '#fff',
    borderRadius: 24,
    padding: 32,
    width: '100%',
    maxWidth: 400,
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 10 },
    shadowOpacity: 0.25,
    shadowRadius: 20,
    elevation: 10,
  },
  iconContainer: {
    marginBottom: 20,
  },
  iconCircle: {
    width: 80,
    height: 80,
    borderRadius: 40,
    backgroundColor: '#ffebee',
    justifyContent: 'center',
    alignItems: 'center',
  },
  title: {
    fontSize: 24,
    fontWeight: '800',
    color: '#1a1a2e',
    marginBottom: 12,
    textAlign: 'center',
  },
  message: {
    fontSize: 16,
    color: '#666',
    textAlign: 'center',
    lineHeight: 24,
    marginBottom: 28,
  },
  buttons: {
    flexDirection: 'row',
    width: '100%',
    gap: 12,
  },
  button: {
    flex: 1,
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
    paddingVertical: 14,
    borderRadius: 12,
  },
  cancelButton: {
    backgroundColor: '#f5f5f5',
    borderWidth: 1,
    borderColor: '#e0e0e0',
  },
  cancelButtonText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#666',
  },
  logoutButton: {
    backgroundColor: '#d32f2f',
  },
  buttonDisabled: {
    opacity: 0.5,
  },
  buttonLoading: {
    backgroundColor: '#a02020',
  },
  logoutButtonText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#fff',
  },
  buttonIcon: {
    marginRight: 6,
  },
});
