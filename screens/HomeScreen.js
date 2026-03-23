import { useRef, useEffect, useState } from 'react';
import {
  View,
  StyleSheet,
  ActivityIndicator,
  Text,
  TouchableOpacity,
  Platform,
  Linking,
  BackHandler,
  Alert,
} from 'react-native';
import { WebView } from 'react-native-webview';
import { StatusBar } from 'expo-status-bar';
import * as Location from 'expo-location';
import * as DocumentPicker from 'expo-document-picker';
import * as Camera from 'expo-camera';
import { File, Paths } from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import NotificationBanner from '../components/NotificationBanner';


// URL schemes that must open in external apps
const EXTERNAL_SCHEMES = [
  'tel:', 'sms:', 'smsto:',
  'whatsapp:',
  'upi:', 'paytm:', 'paytmmp:', 'phonepe:', 'gpay:', 'tez:', 'bhim:', 'amazonpay:',
  'mailto:',
  'intent:',   // Android intent (UPI apps)
  'market:',   // Google Play
];

// File extensions that should be downloaded (not opened in WebView)
const DOWNLOAD_EXTENSIONS = [
  '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.csv',
  '.zip', '.rar', '.ppt', '.pptx', '.txt', '.odt', '.ods',
];

export default function HomeScreen({ user, url: WEB_APP_URL, isMultiUrl, onBackToSelector, onLogout }) {
  const webViewRef = useRef(null);
  const canGoBackRef = useRef(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [notification, setNotification] = useState(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    requestPermissions();
  }, []);

  // Android hardware back button
  useEffect(() => {
    const handler = BackHandler.addEventListener('hardwareBackPress', () => {
      // Still has WebView history → go back within WebView
      if (canGoBackRef.current && webViewRef.current) {
        webViewRef.current.goBack();
        return true;
      }
      // At the first page of the WebView
      if (isMultiUrl) {
        // Multiple URLs → return to URL selector
        onBackToSelector();
        return true;
      }
      // Single URL → confirm logout
      Alert.alert(
        'Logout',
        'Are you sure you want to logout?',
        [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Logout', style: 'destructive', onPress: onLogout },
        ],
        { cancelable: true }
      );
      return true; // always consume — never let OS close the app silently
    });
    return () => handler.remove();
  }, [isMultiUrl, onBackToSelector, onLogout]);

  const requestPermissions = async () => {
    await Location.requestForegroundPermissionsAsync();
    await Camera.Camera.requestCameraPermissionsAsync();
  };

  const showBanner = (title, body) => {
    setNotification({ title, body });
  };

  // ── Deep link handler ───────────────────────────────────────────────────────
  const getAppName = (url) => {
    if (url.startsWith('tel:'))                              return 'Phone';
    if (url.startsWith('sms:') || url.startsWith('smsto:')) return 'Messages';
    if (url.startsWith('whatsapp:') || url.includes('wa.me') || url.includes('api.whatsapp.com')) return 'WhatsApp';
    if (url.startsWith('phonepe:'))                          return 'PhonePe';
    if (url.startsWith('paytm:') || url.startsWith('paytmmp:')) return 'Paytm';
    if (url.startsWith('gpay:') || url.startsWith('tez:'))   return 'Google Pay';
    if (url.startsWith('bhim:'))                             return 'BHIM';
    if (url.startsWith('amazonpay:'))                        return 'Amazon Pay';
    if (url.startsWith('upi:') || url.startsWith('intent:')) return 'UPI App';
    if (url.startsWith('mailto:'))                           return 'Email';
    return 'App';
  };

  const openExternalLink = async (url) => {
    try {
      let targetUrl = url;

      // Convert wa.me → whatsapp:// scheme so Android/iOS can find the app
      if (url.includes('wa.me/')) {
        const phone = url.split('wa.me/')[1].split('?')[0].replace(/\D/g, '');
        targetUrl = `whatsapp://send?phone=${phone}`;
      } else if (url.includes('api.whatsapp.com/send')) {
        targetUrl = url.replace('https://api.whatsapp.com/send', 'whatsapp://send');
      }

      const canOpen = await Linking.canOpenURL(targetUrl);
      if (canOpen) {
        await Linking.openURL(targetUrl);
      } else {
        const appName = getAppName(url);
        showBanner(`${appName} Not Installed`, `Install ${appName} to use this feature.`);
      }
    } catch {
      showBanner('Cannot Open Link', 'Unable to open this link on your device.');
    }
  };

  // ── File download handler ───────────────────────────────────────────────────
  const downloadFile = async (url, filename) => {
    if (downloading) return;
    setDownloading(true);
    showBanner('Downloading...', filename);

    try {
      // Resolve relative URLs
      const fullUrl = url.startsWith('http') ? url : `${WEB_APP_URL}${url.startsWith('/') ? '' : '/'}${url}`;

      // Clean filename
      const cleanName = filename || fullUrl.split('/').pop().split('?')[0] || 'download';

      const destination = new File(Paths.cache, cleanName);
      const downloadedFile = await File.downloadFileAsync(fullUrl, destination);
      const uri = downloadedFile.uri;

      setDownloading(false);
      showBanner('Download Complete', cleanName);

      // Open share sheet so user can save to Files / Drive etc.
      const canShare = await Sharing.isAvailableAsync();
      if (canShare) {
        await Sharing.shareAsync(uri, {
          mimeType: getMimeType(cleanName),
          dialogTitle: `Save ${cleanName}`,
          UTI: getUTI(cleanName),
        });
      }
    } catch (e) {
      setDownloading(false);
      showBanner('Download Failed', 'Could not download the file.');
    }
  };

  const getMimeType = (filename) => {
    const ext = filename.split('.').pop().toLowerCase();
    const map = {
      pdf: 'application/pdf',
      doc: 'application/msword',
      docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      xls: 'application/vnd.ms-excel',
      xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      csv: 'text/csv',
      zip: 'application/zip',
      ppt: 'application/vnd.ms-powerpoint',
      pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      txt: 'text/plain',
    };
    return map[ext] || 'application/octet-stream';
  };

  const getUTI = (filename) => {
    const ext = filename.split('.').pop().toLowerCase();
    const map = {
      pdf: 'com.adobe.pdf',
      doc: 'com.microsoft.word.doc',
      docx: 'org.openxmlformats.wordprocessingml.document',
      xls: 'com.microsoft.excel.xls',
      xlsx: 'org.openxmlformats.spreadsheetml.sheet',
      csv: 'public.comma-separated-values-text',
      zip: 'public.zip-archive',
    };
    return map[ext] || 'public.data';
  };

  // ── Intercept URL before WebView loads it (backup for onShouldStartLoadWithRequest) ──
  const onShouldStartLoadWithRequest = (request) => {
    const { url } = request;

    // 1. Non-HTTP schemes → external app
    if (EXTERNAL_SCHEMES.some((s) => url.startsWith(s))) {
      openExternalLink(url);
      return false;
    }

    // 2. WhatsApp web links
    if (url.includes('wa.me/') || url.includes('api.whatsapp.com/')) {
      openExternalLink(url);
      return false;
    }

    // 3. Direct file URL (href to PDF/xlsx etc. without download attr)
    const lower = url.toLowerCase().split('?')[0];
    if (DOWNLOAD_EXTENSIONS.some((ext) => lower.endsWith(ext))) {
      const filename = url.split('/').pop().split('?')[0];
      downloadFile(url, filename);
      return false;
    }

    return true;
  };

  // ── iOS: WebView native download callback ──────────────────────────────────
  const onFileDownload = ({ nativeEvent }) => {
    const { downloadUrl } = nativeEvent;
    const filename = downloadUrl.split('/').pop().split('?')[0] || 'download';
    downloadFile(downloadUrl, filename);
  };

  // ── Injected JS bridges ────────────────────────────────────────────────────
  const injectedJavaScript = `
    (function () {
      window.__MS_USER__ = ${JSON.stringify({
        username: user?.username,
        deviceId: user?.deviceId,
        fcmToken: user?.fcmToken || null,
      })};

      // ── Notification bridge ──────────────────────────────────────────────
      window.Notification = function (title, options) {
        options = options || {};
        window.ReactNativeWebView.postMessage(JSON.stringify({
          type: 'SHOW_NOTIFICATION',
          title: String(title || ''),
          body: String(options.body || ''),
        }));
        return { close: function(){}, onclick: null, onclose: null };
      };
      window.Notification.permission = 'granted';
      window.Notification.requestPermission = function () { return Promise.resolve('granted'); };

      // ── Geolocation bridge ───────────────────────────────────────────────
      Object.defineProperty(navigator, 'geolocation', {
        value: {
          getCurrentPosition: function (success, error, opts) {
            window._geoSuccess = success; window._geoError = error;
            window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'GET_LOCATION' }));
          },
          watchPosition: function (success, error, opts) {
            window._geoWatchSuccess = success;
            window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'WATCH_LOCATION' }));
            return 1;
          },
          clearWatch: function () {
            window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'CLEAR_WATCH' }));
          },
        },
        configurable: true,
      });

      // ── window.open override (JS-triggered popups / deep links) ──────────
      var _winOpen = window.open;
      window.open = function (url, target, features) {
        if (!url) return _winOpen.call(window, url, target, features);
        var deepSchemes = ['tel:','sms:','smsto:','whatsapp:','upi:','paytm:','paytmmp:','phonepe:','gpay:','tez:','bhim:','intent:','mailto:'];
        if (deepSchemes.some(function(s){ return url.startsWith(s); }) ||
            url.indexOf('wa.me/') !== -1 || url.indexOf('api.whatsapp.com/') !== -1) {
          window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'OPEN_EXTERNAL', url: url }));
          return null;
        }
        return _winOpen.call(window, url, target, features);
      };

      // ── Unified click handler (deep links + downloads + file picker) ──────
      // NOTE: Uses getAttribute('href') — NOT el.href — because el.href
      //       resolves "tel:0123" into a full HTTP URL, breaking scheme checks.
      var DEEP_SCHEMES = ['tel:','sms:','smsto:','whatsapp:','upi:','paytm:','paytmmp:','phonepe:','gpay:','tez:','bhim:','amazonpay:','intent:','market:','mailto:'];
      var DL_EXTS = ['.pdf','.doc','.docx','.xls','.xlsx','.csv','.zip','.rar','.ppt','.pptx','.txt','.odt','.ods'];

      document.addEventListener('click', function (e) {
        // ── File input ──────────────────────────────────────────────────────
        var inputEl = e.target;
        if (inputEl.tagName === 'INPUT' && inputEl.type === 'file') {
          e.preventDefault();
          window.ReactNativeWebView.postMessage(JSON.stringify({
            type: inputEl.getAttribute('capture') !== null ? 'OPEN_CAMERA' : 'OPEN_FILE_PICKER',
            accept: inputEl.accept || '*/*',
          }));
          return;
        }

        // ── Anchor links ────────────────────────────────────────────────────
        var el = e.target.closest('a');
        if (!el) return;

        // Raw href (preserves tel:, upi:, intent: etc.)
        var rawHref = el.getAttribute('href') || '';
        // Resolved href (for http/https + WhatsApp web links)
        var resolvedHref = el.href || rawHref;

        // 1. Deep link scheme in raw href → external app
        if (DEEP_SCHEMES.some(function(s){ return rawHref.startsWith(s); })) {
          e.preventDefault(); e.stopPropagation();
          window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'OPEN_EXTERNAL', url: rawHref }));
          return;
        }

        // 2. WhatsApp web links (https://wa.me/... or https://api.whatsapp.com/...)
        if (resolvedHref.indexOf('wa.me/') !== -1 || resolvedHref.indexOf('api.whatsapp.com/') !== -1) {
          e.preventDefault(); e.stopPropagation();
          window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'OPEN_EXTERNAL', url: resolvedHref }));
          return;
        }

        // 3. Download file (download attr or recognised extension)
        var downloadAttr = el.getAttribute('download');
        var lower = resolvedHref.toLowerCase().split('?')[0];
        var isFile = DL_EXTS.some(function(ext){ return lower.endsWith(ext); });
        if (downloadAttr !== null || isFile) {
          e.preventDefault(); e.stopPropagation();
          var filename = (downloadAttr && downloadAttr.length > 0)
            ? downloadAttr
            : (resolvedHref.split('/').pop().split('?')[0] || 'download');
          window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'DOWNLOAD_FILE', url: resolvedHref, filename: filename }));
        }
      }, true);

      console.log('[MS] All bridges initialised');
      true;
    })();
  `;

  const handleMessage = async (event) => {
    let msg;
    try { msg = JSON.parse(event.nativeEvent.data); } catch { return; }

    switch (msg.type) {
      case 'SHOW_NOTIFICATION':
        showBanner(msg.title, msg.body);
        break;

      case 'DOWNLOAD_FILE':
        await downloadFile(msg.url, msg.filename);
        break;

      case 'OPEN_EXTERNAL':
        await openExternalLink(msg.url);
        break;

      case 'GET_LOCATION':
      case 'WATCH_LOCATION': {
        const { status } = await Location.requestForegroundPermissionsAsync();
        if (status !== 'granted') {
          webViewRef.current?.injectJavaScript(`window._geoError && window._geoError({code:1,message:'Permission denied'}); true;`);
          return;
        }
        const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
        webViewRef.current?.injectJavaScript(`
          var cb = window._geoSuccess || window._geoWatchSuccess;
          cb && cb({ coords: { latitude: ${loc.coords.latitude}, longitude: ${loc.coords.longitude}, accuracy: ${loc.coords.accuracy}, altitude: ${loc.coords.altitude ?? null}, altitudeAccuracy: ${loc.coords.altitudeAccuracy ?? null}, heading: ${loc.coords.heading ?? null}, speed: ${loc.coords.speed ?? null} }, timestamp: ${loc.timestamp} });
          true;
        `);
        break;
      }

      case 'OPEN_FILE_PICKER': {
        const result = await DocumentPicker.getDocumentAsync({ type: msg.accept || '*/*', copyToCacheDirectory: true });
        if (!result.canceled && result.assets?.length) {
          const file = result.assets[0];
          webViewRef.current?.injectJavaScript(`
            window.__MS_PICKED_FILE__ = { name: '${file.name}', uri: '${file.uri}', mimeType: '${file.mimeType}', size: ${file.size} };
            document.dispatchEvent(new CustomEvent('ms_file_picked', { detail: window.__MS_PICKED_FILE__ }));
            true;
          `);
        }
        break;
      }

      case 'OPEN_CAMERA': {
        const { status } = await Camera.Camera.requestCameraPermissionsAsync();
        if (status !== 'granted') return;
        webViewRef.current?.injectJavaScript(`document.dispatchEvent(new CustomEvent('ms_camera_ready')); true;`);
        break;
      }
    }
  };

  if (error) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>Failed to load the app</Text>
        <Text style={styles.errorDetail}>{error}</Text>
        <Text style={styles.errorUrl}>URL: {WEB_APP_URL}</Text>
        <TouchableOpacity style={styles.retryBtn} onPress={() => setError(null)}>
          <Text style={styles.retryText}>Retry</Text>
        </TouchableOpacity>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <StatusBar style="dark" />

      <NotificationBanner
        notification={notification}
        onDismiss={() => setNotification(null)}
      />

      {loading && (
        <View style={styles.loadingOverlay}>
          <ActivityIndicator size="large" color="#1a1a2e" />
          <Text style={styles.loadingText}>Loading...</Text>
        </View>
      )}

      <WebView
        ref={webViewRef}
        source={{ uri: WEB_APP_URL }}
        style={styles.webview}
        javaScriptEnabled
        domStorageEnabled
        geolocationEnabled
        allowFileAccess
        allowFileAccessFromFileURLs
        allowUniversalAccessFromFileURLs
        allowsInlineMediaPlayback
        mediaPlaybackRequiresUserAction={false}
        mixedContentMode="always"
        injectedJavaScript={injectedJavaScript}
        onMessage={handleMessage}
        onShouldStartLoadWithRequest={onShouldStartLoadWithRequest}
        onFileDownload={onFileDownload}
        onNavigationStateChange={(navState) => { canGoBackRef.current = navState.canGoBack; }}
        onLoadEnd={() => setLoading(false)}
        onError={(syntheticEvent) => {
          const { nativeEvent } = syntheticEvent;
          setLoading(false);
          setError(`${nativeEvent.description || 'Unknown error'} (Code: ${nativeEvent.code || 'N/A'})`);
        }}
        onHttpError={(syntheticEvent) => {
          const { nativeEvent } = syntheticEvent;
          setLoading(false);
          setError(`HTTP ${nativeEvent.statusCode}: ${nativeEvent.description || 'Server error'}`);
        }}
        userAgent={`MSApp/1.0 ReactNative/${Platform.OS}`}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f7fa' },
  webview: { flex: 1 },
  loadingOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: '#f5f7fa',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 10,
  },
  loadingText: { marginTop: 12, color: '#555', fontSize: 14 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  errorText: { fontSize: 18, color: '#d32f2f', marginBottom: 8, fontWeight: '700', textAlign: 'center' },
  errorDetail: { fontSize: 14, color: '#666', marginBottom: 6, textAlign: 'center' },
  errorUrl: { fontSize: 12, color: '#999', marginBottom: 24, textAlign: 'center', paddingHorizontal: 16 },
  retryBtn: { backgroundColor: '#1a1a2e', paddingHorizontal: 28, paddingVertical: 12, borderRadius: 10 },
  retryText: { color: '#fff', fontWeight: '700' },
});
