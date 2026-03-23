import { useEffect, useRef } from 'react';
import { Animated, Text, StyleSheet, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

export default function Toast({ visible, message, type = 'success', duration = 3000 }) {
  const translateY = useRef(new Animated.Value(-100)).current;
  const opacity = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (visible) {
      Animated.parallel([
        Animated.spring(translateY, {
          toValue: 0,
          useNativeDriver: true,
          speed: 20,
          bounciness: 6,
        }),
        Animated.timing(opacity, {
          toValue: 1,
          duration: 200,
          useNativeDriver: true,
        }),
      ]).start();

      const timer = setTimeout(() => {
        Animated.parallel([
          Animated.timing(translateY, {
            toValue: -100,
            duration: 250,
            useNativeDriver: true,
          }),
          Animated.timing(opacity, {
            toValue: 0,
            duration: 250,
            useNativeDriver: true,
          }),
        ]).start();
      }, duration);

      return () => clearTimeout(timer);
    }
  }, [visible, message]);

  const isSuccess = type === 'success';
  const iconName = isSuccess ? 'checkmark-circle' : 'close-circle';
  const iconColor = isSuccess ? '#22c55e' : '#ef4444';
  const borderColor = isSuccess ? '#22c55e' : '#ef4444';

  return (
    <Animated.View
      style={[
        styles.container,
        { transform: [{ translateY }], opacity, borderLeftColor: borderColor },
      ]}
    >
      <Ionicons name={iconName} size={22} color={iconColor} style={styles.icon} />
      <Text style={styles.message} numberOfLines={2}>{message}</Text>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    top: 55,
    right: 16,
    left: 16,
    backgroundColor: '#fff',
    borderRadius: 12,
    borderLeftWidth: 4,
    paddingVertical: 12,
    paddingHorizontal: 14,
    flexDirection: 'row',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.12,
    shadowRadius: 10,
    elevation: 8,
    zIndex: 999,
  },
  icon: {
    marginRight: 10,
  },
  message: {
    flex: 1,
    fontSize: 14,
    fontWeight: '600',
    color: '#1a1a2e',
  },
});
