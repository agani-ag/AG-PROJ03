import { useEffect, useState, useCallback } from 'react';
import { useAppName } from '../utils/AppContext';
import AppBrand from '../components/AppBrand';
import LogoutConfirmation from '../components/LogoutConfirmation';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  FlatList,
  SafeAreaView,
  BackHandler,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { Ionicons } from '@expo/vector-icons';

export default function URLSelectorScreen({ user, urls, onSelect, onLogout, onRefresh }) {
  const entries = Object.entries(urls);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      if (onRefresh) await onRefresh();
    } finally {
      setRefreshing(false);
    }
  }, [onRefresh]);

  // Back on selector screen → confirm logout
  useEffect(() => {
    const handler = BackHandler.addEventListener('hardwareBackPress', () => {
      setShowLogoutConfirm(true);
      return true;
    });
    return () => handler.remove();
  }, []);

  const renderItem = ({ item, index }) => {
    const [label, url] = item;
    return (
      <TouchableOpacity style={styles.card} onPress={() => onSelect(url)} activeOpacity={0.7}>
        <View style={styles.cardIcon}>
          <Ionicons name="globe-outline" size={22} color="#4a90e2" />
        </View>
        <View style={styles.cardText}>
          <Text style={styles.cardLabel}>{label}</Text>
          {/* <Text style={styles.cardUrl} numberOfLines={1}>{url}</Text> */}
        </View>
        <Ionicons name="chevron-forward" size={20} color="#ccc" />
      </TouchableOpacity>
    );
  };

  return (
    <SafeAreaView style={styles.container}>
      <StatusBar style="dark" />

      <LogoutConfirmation
        visible={showLogoutConfirm}
        onCancel={() => setShowLogoutConfirm(false)}
        onConfirm={onLogout}
      />

      <View style={styles.header}>
        <AppBrand textStyle={styles.appName} />
        <Text style={styles.greeting}>Hello, {user?.username}</Text>
        <Text style={styles.subtitle}>Select a workspace to continue</Text>
      </View>

      <FlatList
        data={entries}
        keyExtractor={([label]) => label}
        renderItem={renderItem}
        contentContainerStyle={styles.list}
        showsVerticalScrollIndicator={false}
        refreshing={refreshing}
        onRefresh={handleRefresh}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#f5f7fa',
  },
  header: {
    alignItems: 'center',
    paddingTop: 48,
    paddingBottom: 32,
    paddingHorizontal: 24,
  },
  appName: {
    fontSize: 44,
    fontWeight: '800',
    color: '#1a1a2e',
    letterSpacing: 2,
    marginBottom: 12,
  },
  greeting: {
    fontSize: 18,
    fontWeight: '700',
    color: '#1a1a2e',
  },
  subtitle: {
    fontSize: 14,
    color: '#888',
    marginTop: 4,
  },
  list: {
    paddingHorizontal: 20,
    paddingBottom: 32,
  },
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
    borderRadius: 14,
    padding: 16,
    marginBottom: 12,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.06,
    shadowRadius: 8,
    elevation: 3,
  },
  cardIcon: {
    width: 44,
    height: 44,
    borderRadius: 11,
    backgroundColor: '#eef4fd',
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 14,
  },
  cardText: {
    flex: 1,
  },
  cardLabel: {
    fontSize: 15,
    fontWeight: '700',
    color: '#1a1a2e',
    marginBottom: 3,
  },
  cardUrl: {
    fontSize: 12,
    color: '#999',
  },
});
