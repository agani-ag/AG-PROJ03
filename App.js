import { useState } from 'react';
import LoginScreen from './screens/LoginScreen';
import URLSelectorScreen from './screens/URLSelectorScreen';
import HomeScreen from './screens/HomeScreen';

export default function App() {
  const [user, setUser] = useState(null);
  const [webUrl, setWebUrl] = useState(null);

  const handleLoginSuccess = (userData) => {
    setUser(userData);
    const entries = Object.entries(userData.urls || {});
    if (entries.length === 1) {
      setWebUrl(entries[0][1]); // single URL → go straight to WebView
    }
    // multiple URLs → webUrl stays null → URLSelectorScreen shown
  };

  const handleLogout = () => {
    setUser(null);
    setWebUrl(null);
  };

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
