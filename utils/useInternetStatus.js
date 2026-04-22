import { useEffect, useState, useRef } from 'react';
import { AppState } from 'react-native';
import * as Network from 'expo-network';

/**
 * Hook that tracks foreground internet connectivity.
 *
 * Strategy:
 *  1. Subscribe to Network.addNetworkStateListener for instant OS-level events
 *     (wifi toggle, airplane mode, mobile data off, cable unplug).
 *  2. Poll every 5s while app is in foreground as a safety net — some OEMs
 *     don't reliably fire listener events for all transitions.
 *  3. Consider "online" only when BOTH isConnected AND isInternetReachable
 *     are true. Treat `isInternetReachable === null/undefined` as online
 *     (expo-network sometimes returns undefined on first read).
 *  4. Pause polling when app goes to background; refresh immediately on
 *     foreground resume.
 *
 * Returns: { isOnline, isChecking, lastChecked }
 */
export function useInternetStatus(pollIntervalMs = 5000) {
  const [isOnline, setIsOnline] = useState(true);
  const [isChecking, setIsChecking] = useState(true);
  const [lastChecked, setLastChecked] = useState(null);
  const pollTimerRef = useRef(null);
  const listenerRef = useRef(null);
  const mountedRef = useRef(true);

  const evaluateState = (state) => {
    if (!state) return true; // be lenient on missing data
    const connected = state.isConnected === true;
    // isInternetReachable can be null on first check — don't flag offline
    // purely based on that. Require explicit false.
    const reachable = state.isInternetReachable !== false;
    return connected && reachable;
  };

  const checkNow = async () => {
    try {
      const state = await Network.getNetworkStateAsync();
      if (!mountedRef.current) return;
      setIsOnline(evaluateState(state));
      setLastChecked(new Date());
      setIsChecking(false);
    } catch {
      // If the native module fails, don't block the user
      if (!mountedRef.current) return;
      setIsOnline(true);
      setIsChecking(false);
    }
  };

  const startPolling = () => {
    stopPolling();
    pollTimerRef.current = setInterval(checkNow, pollIntervalMs);
  };

  const stopPolling = () => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  };

  useEffect(() => {
    mountedRef.current = true;

    // Initial check
    checkNow();

    // Subscribe to instant OS events
    try {
      if (typeof Network.addNetworkStateListener === 'function') {
        listenerRef.current = Network.addNetworkStateListener((state) => {
          if (!mountedRef.current) return;
          setIsOnline(evaluateState(state));
          setLastChecked(new Date());
        });
      }
    } catch {}

    // Poll while foreground
    startPolling();

    // Handle app state transitions — stop polling in bg, resume in fg
    const appSub = AppState.addEventListener('change', (next) => {
      if (next === 'active') {
        checkNow();
        startPolling();
      } else {
        stopPolling();
      }
    });

    return () => {
      mountedRef.current = false;
      stopPolling();
      appSub?.remove?.();
      try {
        listenerRef.current?.remove?.();
      } catch {}
    };
  }, [pollIntervalMs]);

  return { isOnline, isChecking, lastChecked, recheck: checkNow };
}
