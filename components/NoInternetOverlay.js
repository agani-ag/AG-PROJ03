import { View, Text, StyleSheet, TouchableOpacity, ActivityIndicator } from 'react-native';

/**
 * Full-screen blocking overlay shown when internet is unavailable.
 * Rendered ABOVE all app content so taps cannot reach the UI behind it.
 */
export default function NoInternetOverlay({ onRetry, isChecking }) {
  return (
    <View style={styles.root} pointerEvents="auto">
      {/* Diagonal watermark strip across the screen */}
      <View style={styles.watermarkWrap} pointerEvents="none">
        <Text style={styles.watermarkText}>NO INTERNET</Text>
      </View>

      <View style={styles.card}>
        <View style={styles.iconCircle}>
          <Text style={styles.icon}>📡</Text>
        </View>

        <Text style={styles.title}>No Internet Connection</Text>
        <Text style={styles.subtitle}>
          This app requires an active internet connection to continue.
          Please check your Wi-Fi or mobile data and try again.
        </Text>

        <View style={styles.tipsBox}>
          <Text style={styles.tipLine}>• Turn Wi-Fi on or connect to a network</Text>
          <Text style={styles.tipLine}>• Disable airplane mode</Text>
          <Text style={styles.tipLine}>• Check your mobile data</Text>
        </View>

        <TouchableOpacity
          style={styles.retryBtn}
          onPress={onRetry}
          disabled={isChecking}
          activeOpacity={0.7}
        >
          {isChecking ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.retryText}>Retry</Text>
          )}
        </TouchableOpacity>

        <Text style={styles.autoNote}>
          The app will resume automatically once the connection is restored.
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: 'rgba(10, 15, 25, 0.96)',
    zIndex: 9999,
    elevation: 30,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 24,
  },
  watermarkWrap: {
    position: 'absolute',
    top: '45%',
    left: -100,
    right: -100,
    transform: [{ rotate: '-20deg' }],
    alignItems: 'center',
    justifyContent: 'center',
  },
  watermarkText: {
    fontSize: 72,
    fontWeight: '900',
    color: 'rgba(255, 80, 80, 0.12)',
    letterSpacing: 8,
    textAlign: 'center',
  },
  card: {
    backgroundColor: '#ffffff',
    borderRadius: 18,
    paddingVertical: 28,
    paddingHorizontal: 22,
    width: '100%',
    maxWidth: 400,
    alignItems: 'center',
    shadowColor: '#000',
    shadowOpacity: 0.4,
    shadowRadius: 20,
    shadowOffset: { width: 0, height: 8 },
    elevation: 12,
  },
  iconCircle: {
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: '#fff4f4',
    borderWidth: 2,
    borderColor: '#ffb3b3',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 14,
  },
  icon: { fontSize: 34 },
  title: {
    fontSize: 20,
    fontWeight: '700',
    color: '#1a1a1a',
    marginBottom: 8,
    textAlign: 'center',
  },
  subtitle: {
    fontSize: 14,
    color: '#555',
    textAlign: 'center',
    lineHeight: 20,
    marginBottom: 16,
  },
  tipsBox: {
    backgroundColor: '#f6f8fb',
    borderRadius: 10,
    padding: 12,
    alignSelf: 'stretch',
    marginBottom: 18,
  },
  tipLine: {
    fontSize: 13,
    color: '#333',
    marginVertical: 2,
  },
  retryBtn: {
    backgroundColor: '#d9534f',
    paddingVertical: 12,
    paddingHorizontal: 40,
    borderRadius: 10,
    minWidth: 160,
    alignItems: 'center',
    marginBottom: 10,
  },
  retryText: {
    color: '#fff',
    fontWeight: '700',
    fontSize: 15,
    letterSpacing: 0.5,
  },
  autoNote: {
    fontSize: 11,
    color: '#888',
    textAlign: 'center',
    fontStyle: 'italic',
  },
});
