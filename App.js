import { useState, useCallback } from 'react';
import { AppProvider } from './utils/AppContext';
import { ApiConfigProvider } from './utils/ApiConfig';
import LoginScreen from './screens/LoginScreen';
import URLSelectorScreen from './screens/URLSelectorScreen';
import HomeScreen from './screens/HomeScreen';

function RootNavigator() {
  const [user, setUser] = useState(null);
  const [webUrl, setWebUrl] = useState(null);

  const handleLoginSuccess = (userData) => {
    setUser(userData);
    const entries = Object.entries(userData.urls || {});
    if (entries.length === 1) {
      setWebUrl(entries[0][1]);
    }
  };

  const handleLogout = useCallback(() => {
    setUser(null);
    setWebUrl(null);
  }, []);

  const isMultiUrl = Object.keys(user?.urls || {}).length > 1;

  if (!user) {
    return <LoginScreen onLoginSuccess={handleLoginSuccess} />;
  }

  if (!webUrl) {
    return (
      <URLSelectorScreen
        user={user}
        urls={user.urls}
        onSelect={setWebUrl}
        onLogout={handleLogout}
      />
    );
  }

  return (
    <HomeScreen
      user={user}
      url={webUrl}
      isMultiUrl={isMultiUrl}
      onBackToSelector={() => setWebUrl(null)}
      onLogout={handleLogout}
    />
  );
}

export default function App() {
  return (
    <ApiConfigProvider>
      <AppProvider>
        <RootNavigator />
      </AppProvider>
    </ApiConfigProvider>
  );
}
