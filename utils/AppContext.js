import { createContext, useContext, useState, useEffect } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';

const KEY_NAME = 'syncup_business_name';
const DEFAULT_NAME = 'SyncUp';

const AppContext = createContext({
  appName: DEFAULT_NAME,
  updateAppName: async () => {},
});

export function AppProvider({ children }) {
  const [appName, setAppName] = useState(DEFAULT_NAME);

  // Load persisted name when app starts
  useEffect(() => {
    AsyncStorage.getItem(KEY_NAME).then((savedName) => {
      if (savedName && savedName.trim()) setAppName(savedName.trim());
    });
  }, []);

  const updateAppName = async (name) => {
    const trimmed = (name || 'SyncUp').trim() || 'SyncUp';
    setAppName(trimmed);
    await AsyncStorage.setItem(KEY_NAME, trimmed);
  };

  return (
    <AppContext.Provider value={{ appName, updateAppName }}>
      {children}
    </AppContext.Provider>
  );
}

export const useAppName = () => useContext(AppContext);
