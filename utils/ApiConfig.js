import { createContext, useContext, useState, useEffect } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Platform } from 'react-native';
import { DEFAULT_API_BASE, DEFAULT_FALLBACK } from '@env';

const KEY_API_BASE = 'syncup_api_base';
const KEY_FALLBACK = 'syncup_api_fallback';

const ApiConfigContext = createContext({
  apiBase: DEFAULT_API_BASE,
  fallbackUrl: DEFAULT_FALLBACK,
  currentUrl: DEFAULT_API_BASE,
  isUsingFallback: false,
  updateApiBase: async () => {},
  updateFallback: async () => {},
  checkHealth: async () => {},
  resetToDefaults: async () => {},
});

export function ApiConfigProvider({ children }) {
  const [apiBase, setApiBase] = useState(DEFAULT_API_BASE);
  const [fallbackUrl, setFallbackUrl] = useState(DEFAULT_FALLBACK);
  const [currentUrl, setCurrentUrl] = useState(DEFAULT_API_BASE);
  const [isUsingFallback, setIsUsingFallback] = useState(false);

  // Load saved URLs on mount
  useEffect(() => {
    Promise.all([
      AsyncStorage.getItem(KEY_API_BASE),
      AsyncStorage.getItem(KEY_FALLBACK),
    ]).then(([savedBase, savedFallback]) => {
      if (savedBase && savedBase.trim()) {
        setApiBase(savedBase.trim());
        setCurrentUrl(savedBase.trim());
      }
      if (savedFallback && savedFallback.trim()) {
        setFallbackUrl(savedFallback.trim());
      }
    });
  }, []);

  const updateApiBase = async (url) => {
    const trimmed = url?.trim() || DEFAULT_API_BASE;
    setApiBase(trimmed);
    setCurrentUrl(trimmed);
    setIsUsingFallback(false);
    await AsyncStorage.setItem(KEY_API_BASE, trimmed);
  };

  const updateFallback = async (url) => {
    const trimmed = url?.trim() || DEFAULT_FALLBACK;
    setFallbackUrl(trimmed);
    await AsyncStorage.setItem(KEY_FALLBACK, trimmed);
  };

  /**
   * Health check — tries primary URL first, falls back if it fails.
   * Returns the working URL.
   */
  const checkHealth = async () => {
    // Try primary URL
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);

      const res = await fetch(`${apiBase}/device/api/health`, {
        method: 'GET',
        signal: controller.signal,
      });

      clearTimeout(timeout);

      if (res.ok) {
        var json = await res.json();
        // Save fallback URL from response
        if (json.fallback_url) {
          await updateFallback(json.fallback_url);
        }
        setCurrentUrl(apiBase);
        setIsUsingFallback(false);
        return apiBase;
      }
    } catch (err) {
      console.log('[Health] Primary URL failed, trying fallback...');
    }

    // Try fallback URL
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);

      const res = await fetch(`${fallbackUrl}/device/api/health`, {
        method: 'GET',
        signal: controller.signal,
      });

      clearTimeout(timeout);

      if (res.ok) {
        var json = await res.json();
        // Save fallback URL from response
        if (json.fallback_url) {
          await updateFallback(json.fallback_url);
        }
        setCurrentUrl(fallbackUrl);
        setIsUsingFallback(true);
        return fallbackUrl;
      }
    } catch (err) {
      console.log('[Health] Fallback URL also failed');
    }

    // Both failed — stay with primary
    setCurrentUrl(apiBase);
    setIsUsingFallback(false);
    return apiBase;
  };

  const resetToDefaults = async () => {
    setApiBase(DEFAULT_API_BASE);
    setFallbackUrl(DEFAULT_FALLBACK);
    setCurrentUrl(DEFAULT_API_BASE);
    setIsUsingFallback(false);
    await AsyncStorage.removeItem(KEY_API_BASE);
    await AsyncStorage.removeItem(KEY_FALLBACK);
  };

  return (
    <ApiConfigContext.Provider
      value={{
        apiBase,
        fallbackUrl,
        currentUrl,
        isUsingFallback,
        updateApiBase,
        updateFallback,
        checkHealth,
        resetToDefaults,
      }}
    >
      {children}
    </ApiConfigContext.Provider>
  );
}

export const useApiConfig = () => useContext(ApiConfigContext);
