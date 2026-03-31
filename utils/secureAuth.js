import * as SecureStore from 'expo-secure-store';
import * as LocalAuthentication from 'expo-local-authentication';
import { Platform } from 'react-native';

const CRED_KEY = 'user_credentials';
const REMEMBER_KEY = 'remember_me';

/**
 * Check if device has biometric or screen lock security
 */
export async function checkDeviceSecurity() {
  try {
    // Check if device has any authentication hardware
    const hasHardware = await LocalAuthentication.hasHardwareAsync();
    if (!hasHardware) {
      return { hasSecurity: false, type: 'none' };
    }

    // Check if device is secured (biometric or PIN/pattern)
    const isEnrolled = await LocalAuthentication.isEnrolledAsync();
    if (!isEnrolled) {
      return { hasSecurity: false, type: 'none' };
    }

    // Check what types are available
    const types = await LocalAuthentication.supportedAuthenticationTypesAsync();

    if (types.includes(LocalAuthentication.AuthenticationType.FINGERPRINT)) {
      return { hasSecurity: true, type: 'fingerprint' };
    }
    if (types.includes(LocalAuthentication.AuthenticationType.FACIAL_RECOGNITION)) {
      return { hasSecurity: true, type: 'face' };
    }
    if (types.includes(LocalAuthentication.AuthenticationType.IRIS)) {
      return { hasSecurity: true, type: 'iris' };
    }

    // Device has PIN/Pattern
    return { hasSecurity: true, type: 'pin' };
  } catch (err) {
    console.error('[Auth] Device security check error:', err);
    return { hasSecurity: false, type: 'none' };
  }
}

/**
 * Authenticate user with biometric or device lock
 */
export async function authenticateWithDevice() {
  try {
    const security = await checkDeviceSecurity();

    if (!security.hasSecurity) {
      // No security set up - allow access directly
      console.log('[Auth] No device security, allowing access');
      return { success: true, noSecurity: true };
    }

    // Prompt for biometric/PIN authentication
    let promptMessage = 'Unlock to access SyncUp';
    if (security.type === 'fingerprint') {
      promptMessage = 'Scan fingerprint to unlock';
    } else if (security.type === 'face') {
      promptMessage = 'Face ID to unlock';
    } else if (security.type === 'pin') {
      promptMessage = 'Enter device PIN to unlock';
    }

    const result = await LocalAuthentication.authenticateAsync({
      promptMessage,
      fallbackLabel: 'Use Passcode',
      cancelLabel: 'Cancel',
      disableDeviceFallback: false,
    });

    if (result.success) {
      console.log('[Auth] Device authentication successful');
      return { success: true, noSecurity: false };
    } else {
      console.log('[Auth] Device authentication failed');
      return { success: false, error: 'Authentication failed' };
    }
  } catch (err) {
    console.error('[Auth] Device authentication error:', err);
    return { success: false, error: err.message };
  }
}

/**
 * Save user credentials securely
 */
export async function saveCredentials(email, password, loginMode = 'S1') {
  try {
    const data = JSON.stringify({ email, password, loginMode, savedAt: Date.now() });
    await SecureStore.setItemAsync(CRED_KEY, data);
    await SecureStore.setItemAsync(REMEMBER_KEY, 'true');
    console.log('[Auth] Credentials and login mode saved securely');
    return true;
  } catch (err) {
    console.error('[Auth] Save credentials error:', err);
    return false;
  }
}

/**
 * Get stored credentials and login mode
 */
export async function getStoredCredentials() {
  try {
    const rememberMe = await SecureStore.getItemAsync(REMEMBER_KEY);
    if (rememberMe !== 'true') {
      return null;
    }

    const data = await SecureStore.getItemAsync(CRED_KEY);
    if (!data) {
      return null;
    }

    const creds = JSON.parse(data);
    console.log('[Auth] Retrieved stored credentials and login mode');
    return { email: creds.email, password: creds.password, loginMode: creds.loginMode };
  } catch (err) {
    console.error('[Auth] Get credentials error:', err);
    return null;
  }
}

/**
 * Clear stored credentials (on logout)
 */
export async function clearStoredCredentials() {
  try {
    await SecureStore.deleteItemAsync(CRED_KEY);
    await SecureStore.deleteItemAsync(REMEMBER_KEY);
    console.log('[Auth] Credentials cleared');
    return true;
  } catch (err) {
    console.error('[Auth] Clear credentials error:', err);
    return false;
  }
}

/**
 * Check if user has saved credentials
 */
export async function hasStoredCredentials() {
  try {
    const rememberMe = await SecureStore.getItemAsync(REMEMBER_KEY);
    return rememberMe === 'true';
  } catch (err) {
    return false;
  }
}
