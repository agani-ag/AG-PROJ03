import { useEffect, useRef } from 'react';
import { Animated, Text, StyleSheet, View, TouchableOpacity } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

export default function NotificationBanner({ notification, onDismiss }) {
  const translateY = useRef(new Animated.Value(-120)).current;
  const opacity = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (!notification) return;

    // Slide in
    Animated.parallel([
      Animated.spring(translateY, {
        toValue: 0,
        useNativeDriver: true,
        speed: 18,
        bounciness: 5,
      }),
      Animated.timing(opacity, {
        toValue: 1,
        duration: 200,
        useNativeDriver: true,
      }),
    ]).start();

    // Auto dismiss after 4s
    const timer = setTimeout(() => dismiss(), 4000);
    return () => clearTimeout(timer);
  }, [notification]);

  const dismiss = () => {
    Animated.parallel([
      Animated.timing(translateY, {
        toValue: -120,
        duration: 280,
        useNativeDriver: true,
      }),
      Animated.timing(opacity, {
        toValue: 0,
        duration: 280,
        useNativeDriver: true,
      }),
    ]).start(() => onDismiss && onDismiss());
  };

  if (!notification) return null;

  return (
    <Animated.View style={[styles.container, { transform: [{ translateY }], opacity }]}>
      <View style={styles.iconBox}>
        <Ionicons name="notifications" size={20} color="#fff" />
      </View>
      <View style={styles.textBox}>
        {notification.title ? (
          <Text style={styles.title} numberOfLines={1}>{notification.title}</Text>
        ) : null}
        {notification.body ? (
          <Text style={styles.body} numberOfLines={2}>{notification.body}</Text>
        ) : null}
      </View>
      <TouchableOpacity onPress={dismiss} style={styles.closeBtn}>
        <Ionicons name="close" size={18} color="#aaa" />
      </TouchableOpacity>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    top: 48,
    left: 12,
    right: 12,
    backgroundColor: '#1a1a2e',
    borderRadius: 14,
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
    paddingHorizontal: 14,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.2,
    shadowRadius: 12,
    elevation: 10,
    zIndex: 9999,
  },
  iconBox: {
    width: 36,
    height: 36,
    borderRadius: 10,
    backgroundColor: '#4a90e2',
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 12,
  },
  textBox: { flex: 1 },
  title: { color: '#fff', fontWeight: '700', fontSize: 14, marginBottom: 2 },
  body: { color: '#ccc', fontSize: 12, lineHeight: 17 },
  closeBtn: { padding: 4, marginLeft: 8 },
});
