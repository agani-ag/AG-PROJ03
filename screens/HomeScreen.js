import { useRef, useEffect, useState, useCallback } from 'react';
import {
  View,
  StyleSheet,
  ActivityIndicator,
  Text,
  TouchableOpacity,
  Platform,
  Linking,
  BackHandler,
} from 'react-native';
import { WebView } from 'react-native-webview';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as Location from 'expo-location';
import * as DocumentPicker from 'expo-document-picker';
import * as Camera from 'expo-camera';
import { File, Paths } from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import * as Print from 'expo-print';
import * as Network from 'expo-network';
import LogoutConfirmation from '../components/LogoutConfirmation';
import { showLocalNotification } from '../utils/notifications';
import * as Notifications from 'expo-notifications';


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

const DOWNLOAD_TIMEOUT_MS = 60000; // 60 second timeout for downloads
const MAX_DOWNLOAD_RETRIES = 2;
const OFFLINE_CHECK_INTERVAL = 5000; // Check every 5 seconds

export default function HomeScreen({ user, url: WEB_APP_URL, isMultiUrl, onBackToSelector, onLogout, notificationTapRef, pendingTapDataRef, showBanner }) {
  const webViewRef = useRef(null);
  const canGoBackRef = useRef(false);
  const canGoForwardRef = useRef(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState(null); // { filename, progress, retryCount }
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const [statusBarColor, setStatusBarColor] = useState('#ffffff');
  const [isOffline, setIsOffline] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

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
      setShowLogoutConfirm(true);
      return true; // always consume — never let OS close the app silently
    });
    return () => handler.remove();
  }, [isMultiUrl, onBackToSelector, onLogout]);

  // ── Offline detection ─────────────────────────────────────────────────────
  useEffect(() => {
    let interval;

    const checkConnection = async () => {
      try {
        const state = await Network.getNetworkStateAsync();
        const offline = !(state.isConnected && state.isInternetReachable);
        setIsOffline(offline);
      } catch {
        setIsOffline(true);
      }
    };

    checkConnection();
    interval = setInterval(checkConnection, OFFLINE_CHECK_INTERVAL);

    return () => clearInterval(interval);
  }, []);

  // ── Pull-to-refresh ───────────────────────────────────────────────────────
  const onRefresh = useCallback(() => {
    if (!webViewRef.current) return;
    setRefreshing(true);
    webViewRef.current.reload();
    setTimeout(() => setRefreshing(false), 1500);
  }, []);

  // Convert CSS color (rgb/rgba/hex) to hex for StatusBar
  const convertCssColorToHex = (cssColor) => {
    if (!cssColor) return '#ffffff';

    // Trim whitespace
    cssColor = cssColor.trim();

    // Already hex
    if (cssColor.startsWith('#')) {
      // Validate and pad hex if needed
      const hex = cssColor.replace('#', '');
      if (hex.length === 3) {
        return '#' + hex.split('').map(c => c + c).join('');
      }
      if (hex.length === 6 || hex.length === 8) {
        return '#' + hex.substring(0, 6);
      }
      return '#ffffff';
    }

    // rgb(r, g, b) or rgba(r, g, b, a) or rgba(r, g, b, a%)
    const rgbaMatch = cssColor.match(/rgba?\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*[\d.%]+)?\s*\)/i);
    if (rgbaMatch) {
      const r = parseInt(rgbaMatch[1]).toString(16).padStart(2, '0');
      const g = parseInt(rgbaMatch[2]).toString(16).padStart(2, '0');
      const b = parseInt(rgbaMatch[3]).toString(16).padStart(2, '0');
      return `#${r}${g}${b}`.toLowerCase();
    }

    // Color names mapping
    const colorMap = {
      'white': '#ffffff', 'black': '#000000', 'red': '#ff0000', 'green': '#00ff00',
      'blue': '#0000ff', 'yellow': '#ffff00', 'cyan': '#00ffff', 'magenta': '#ff00ff',
      'gray': '#808080', 'grey': '#808080', 'transparent': '#ffffff',
    };
    
    const lowerColor = cssColor.toLowerCase();
    if (colorMap[lowerColor]) {
      return colorMap[lowerColor];
    }

    return '#ffffff'; // Default white
  };

  // Check if color is light (to determine status bar text color)
  const isLightColor = (hexColor) => {
    if (!hexColor || hexColor === '#ffffff' || hexColor.toLowerCase() === '#ffffff') return true;

    // Remove # and validate
    const hex = hexColor.replace('#', '').toLowerCase();
    if (hex.length !== 6) return true;

    // Convert hex to RGB
    const r = parseInt(hex.substring(0, 2), 16);
    const g = parseInt(hex.substring(2, 4), 16);
    const b = parseInt(hex.substring(4, 6), 16);

    // Calculate relative luminance (WCAG formula)
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;

    // Return true if light (luminance > 0.5)
    console.log('[StatusBar] Color:', hexColor, 'Luminance:', luminance.toFixed(2), 'Style:', luminance > 0.5 ? 'dark' : 'light');
    return luminance > 0.5;
  };

  // Register notification tap handler so App.js can delegate taps to WebView
  useEffect(() => {
    if (notificationTapRef) {
      notificationTapRef.current = (data) => {
        if (data?.url && webViewRef.current) {
          console.log('[FCM] Navigating WebView to:', data.url);
          webViewRef.current.injectJavaScript(`window.location.href = ${JSON.stringify(data.url)}; true;`);
        }
      };
      return () => { notificationTapRef.current = null; };
    }
  }, [notificationTapRef]);

  // Process any pending notification tap data after WebView loads
  const handleWebViewLoadEnd = () => {
    setLoading(false);
    // Check for pending notification that arrived before HomeScreen mounted
    if (pendingTapDataRef?.current) {
      const pending = pendingTapDataRef.current;
      pendingTapDataRef.current = null;
      console.log('[FCM] Processing pending notification tap:', JSON.stringify(pending));
      if (pending.url && webViewRef.current) {
        setTimeout(() => {
          webViewRef.current.injectJavaScript(`window.location.href = ${JSON.stringify(pending.url)}; true;`);
        }, 500);
      }
    }
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

  // ── File download handler (with progress, timeout & retry) ───────────────
  const downloadFile = async (url, filename, retryCount = 0) => {
    if (downloading && retryCount === 0) return;
    setDownloading(true);
    const cleanName = filename || url.split('/').pop().split('?')[0] || 'download';
    setDownloadProgress({ filename: cleanName, progress: 0, retryCount });

    try {
      const fullUrl = url.startsWith('http') ? url : `${WEB_APP_URL}${url.startsWith('/') ? '' : '/'}${url}`;

      // Race between download and timeout
      const downloadPromise = (async () => {
        const destination = new File(Paths.cache, cleanName);
        setDownloadProgress(prev => ({ ...prev, progress: 10 }));

        const downloadedFile = await File.downloadFileAsync(fullUrl, destination);
        setDownloadProgress(prev => ({ ...prev, progress: 90 }));
        return downloadedFile;
      })();

      const timeoutPromise = new Promise((_, reject) => {
        setTimeout(() => reject(new Error('DOWNLOAD_TIMEOUT')), DOWNLOAD_TIMEOUT_MS);
      });

      const downloadedFile = await Promise.race([downloadPromise, timeoutPromise]);
      const uri = downloadedFile.uri;

      setDownloadProgress(prev => ({ ...prev, progress: 100 }));

      // Brief pause to show 100%
      await new Promise(r => setTimeout(r, 500));
      setDownloading(false);
      setDownloadProgress(null);
      showBanner('Download Complete', cleanName);

      const canShare = await Sharing.isAvailableAsync();
      if (canShare) {
        await Sharing.shareAsync(uri, {
          mimeType: getMimeType(cleanName),
          dialogTitle: `Save ${cleanName}`,
          UTI: getUTI(cleanName),
        });
      }
    } catch (e) {
      const isTimeout = e?.message === 'DOWNLOAD_TIMEOUT';

      if (isTimeout && retryCount < MAX_DOWNLOAD_RETRIES) {
        // Auto-retry on timeout
        setDownloadProgress(prev => ({
          ...prev,
          progress: 0,
          retryCount: retryCount + 1,
        }));
        showBanner('Download Slow', `Retrying... (attempt ${retryCount + 2}/${MAX_DOWNLOAD_RETRIES + 1})`);
        return downloadFile(url, filename, retryCount + 1);
      }

      setDownloading(false);
      setDownloadProgress(null);

      if (isTimeout) {
        showBanner('Download Timed Out', `${cleanName} took too long. Please try again.`);
      } else {
        showBanner('Download Failed', 'Could not download the file.');
      }
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
          icon: String(options.icon || ''),
          image: String(options.image || ''),
        }));
        return { close: function(){}, onclick: null, onclose: null };
      };
      window.Notification.permission = 'granted';
      window.Notification.requestPermission = function () { return Promise.resolve('granted'); };

      // ── Print bridge ─────────────────────────────────────────────────────
      window.print = function () {
        var html = document.documentElement.outerHTML;
        window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'PRINT', html: html }));
      };

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
        // ── Skip Bootstrap / interactive elements ──
        // Check if click target or any ancestor is a Bootstrap toggle, collapse trigger,
        // dropdown, accordion, navbar-toggler, or any element with data-bs-* attributes
        var bsEl = e.target.closest(
          '[data-bs-toggle], [data-bs-dismiss], [data-bs-target], [data-bs-slide], [data-bs-slide-to], ' +
          '[data-toggle], [data-dismiss], [data-target], [data-slide], [data-slide-to], ' +
          '.navbar-toggler, .navbar-toggler-icon, .btn-close, ' +
          '.accordion-button, .carousel-control-prev, .carousel-control-next, ' +
          'button[aria-expanded], [role="tab"], [role="button"]'
        );
        if (bsEl) {
          console.log('[MS] Skipping Bootstrap element:', bsEl.tagName, bsEl.className);
          return; // Let Bootstrap handle it natively
        }

        // ── Anchor links ────────────────────────────────────────────────────
        var el = e.target.closest('a');
        if (!el) return;

        // Raw href (preserves tel:, upi:, intent: etc.)
        var rawHref = el.getAttribute('href') || '';
        // Resolved href (for http/https + WhatsApp web links)
        var resolvedHref = el.href || rawHref;

        // Skip anchors that are Bootstrap toggles (e.g. dropdown triggers wrapped in <a>)
        if (el.getAttribute('data-bs-toggle') || el.getAttribute('data-toggle')) {
          console.log('[MS] Skipping Bootstrap anchor toggle:', rawHref);
          return;
        }

        // Skip empty/hash-only hrefs (likely JS-driven UI elements)
        if (!rawHref || rawHref === '#' || rawHref === 'javascript:void(0)' || rawHref === 'javascript:;') {
          return; // Let the page's own JS handle it
        }

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

      // ── Enable fullscreen for video players (YouTube, Vimeo, etc.) ────────
      (function enableVideoFullscreen() {
        // Allow fullscreen attribute
        var observer = new MutationObserver(function(mutations) {
          mutations.forEach(function(mutation) {
            if (mutation.type === 'childList') {
              var iframes = document.querySelectorAll('iframe');
              iframes.forEach(function(iframe) {
                if (!iframe.getAttribute('allowfullscreen') && !iframe.getAttribute('webkitallowfullscreen')) {
                  iframe.setAttribute('allowfullscreen', 'true');
                  iframe.setAttribute('webkitallowfullscreen', 'true');
                  iframe.setAttribute('mozallowfullscreen', 'true');
                  iframe.setAttribute('allow', 'fullscreen');
                }
              });
            }
          });
        });

        observer.observe(document.body, {
          childList: true,
          subtree: true,
          attributes: false
        });

        // Add fullscreen to existing iframes
        var iframes = document.querySelectorAll('iframe');
        iframes.forEach(function(iframe) {
          iframe.setAttribute('allowfullscreen', 'true');
          iframe.setAttribute('webkitallowfullscreen', 'true');
          iframe.setAttribute('mozallowfullscreen', 'true');
          iframe.setAttribute('allow', 'fullscreen');
        });

        console.log('[MS] Video fullscreen enabled');
      })();

      console.log('[MS] All bridges initialised');

      // ──────────────────────────────────────────────────────────────────────
      // ENHANCED STATUS BAR COLOR DETECTION (with frequent polling)
      // ──────────────────────────────────────────────────────────────────────
      (function detectStatusBarColor() {
        var lastColor = null;
        var pollInterval = null;

        function isValidBgColor(c) {
          return c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent' && c !== 'initial' && c !== 'inherit';
        }

        function getElementBgColor(el) {
          if (!el) return null;
          try {
            var style = window.getComputedStyle(el);
            var bg = style.backgroundColor;
            if (isValidBgColor(bg)) return bg;
          } catch(e) {}
          return null;
        }

        function walkUpForColor(el) {
          var current = el;
          var depth = 0;
          while (current && depth < 15) {
            var color = getElementBgColor(current);
            if (color) return color;
            current = current.parentElement;
            depth++;
          }
          return null;
        }

        function getBackgroundColor() {
          // 1. Check meta theme-color tag (highest priority)
          var metaTheme = document.querySelector('meta[name="theme-color"]');
          if (metaTheme && metaTheme.content) {
            return metaTheme.content;
          }

          // 2. Sample the actual topmost visible element at the status bar area
          //    Check multiple points across the top to find the header/navbar color
          var samplePoints = [
            { x: Math.floor(window.innerWidth / 2), y: 5 },
            { x: Math.floor(window.innerWidth / 4), y: 5 },
            { x: Math.floor(window.innerWidth * 3 / 4), y: 5 },
            { x: Math.floor(window.innerWidth / 2), y: 20 },
            { x: Math.floor(window.innerWidth / 2), y: 40 },
          ];

          for (var i = 0; i < samplePoints.length; i++) {
            try {
              var el = document.elementFromPoint(samplePoints[i].x, samplePoints[i].y);
              if (el) {
                var color = walkUpForColor(el);
                if (color) return color;
              }
            } catch(e) {}
          }

          // 3. Check common header/nav/toolbar elements
          var headerSelectors = [
            'header', 'nav', '[role="banner"]', '[role="navigation"]',
            '.header', '.navbar', '.toolbar', '.app-bar', '.topbar',
            '#header', '#navbar', '#toolbar',
          ];
          for (var j = 0; j < headerSelectors.length; j++) {
            try {
              var headerEl = document.querySelector(headerSelectors[j]);
              if (headerEl) {
                var headerColor = getElementBgColor(headerEl);
                if (headerColor) return headerColor;
              }
            } catch(e) {}
          }

          // 4. Check body background color
          var body = document.body;
          if (body) {
            var bgColor = getElementBgColor(body);
            if (bgColor) return bgColor;
          }

          // 5. Check html element background
          var html = document.documentElement;
          if (html) {
            var htmlBg = getElementBgColor(html);
            if (htmlBg) return htmlBg;
          }

          // 6. Check light/dark mode preference
          if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
            return '#1a1a2e';
          }

          return '#ffffff';
        }

        function sendColorToApp() {
          var color = getBackgroundColor();
          
          // Only send if color actually changed
          if (color !== lastColor) {
            lastColor = color;
            console.log('[StatusBar] Detected color:', color);
            try {
              window.ReactNativeWebView.postMessage(JSON.stringify({
                type: 'STATUS_BAR_COLOR',
                color: color
              }));
            } catch(e) {
              console.log('[StatusBar] Error sending color:', e);
            }
          }
        }

        // Send initial color immediately and after page fully loads
        sendColorToApp();
        document.addEventListener('DOMContentLoaded', sendColorToApp);
        window.addEventListener('load', sendColorToApp);
        document.addEventListener('readystatechange', sendColorToApp);
        setTimeout(sendColorToApp, 100);
        setTimeout(sendColorToApp, 500);
        setTimeout(sendColorToApp, 1500);

        // Polling every 2 seconds (catches dynamic color changes)
        pollInterval = setInterval(sendColorToApp, 2000);

        // Watch for color changes on body and html
        var observer = new MutationObserver(function() {
          sendColorToApp();
        });

        if (document.body) {
          observer.observe(document.body, {
            attributes: true,
            attributeFilter: ['style', 'class'],
            subtree: false,
            characterData: false
          });
        }

        observer.observe(document.documentElement, {
          attributes: true,
          attributeFilter: ['style', 'class'],
          subtree: false,
          characterData: false
        });

        // Listen for theme color meta tag changes
        var headElement = document.head || document.getElementsByTagName('head')[0];
        if (headElement) {
          var metaObserver = new MutationObserver(sendColorToApp);
          metaObserver.observe(headElement, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ['content', 'name']
          });
        }

        // Listen to media query changes (dark mode toggle)
        if (window.matchMedia) {
          try {
            window.matchMedia('(prefers-color-scheme: dark)').addListener(sendColorToApp);
          } catch(e) {}
        }

        // Cleanup on page unload
        window.addEventListener('beforeunload', function() {
          if (pollInterval) clearInterval(pollInterval);
        });
      })();

      true;

      true;
    })();
  `;

  const handleMessage = async (event) => {
    let msg;
    try { msg = JSON.parse(event.nativeEvent.data); } catch { return; }

    switch (msg.type) {
      case 'STATUS_BAR_COLOR':
        // Convert CSS color to hex for StatusBar
        const hexColor = convertCssColorToHex(msg.color);
        console.log('[StatusBar] Received color from webpage:', msg.color, '→ Converted to:', hexColor);
        setStatusBarColor(hexColor);
        break;

      case 'SHOW_NOTIFICATION': {
        // Check if Android system notifications are enabled
        const { status: notifStatus } = await Notifications.getPermissionsAsync();
        if (notifStatus === 'granted') {
          // Show in Android notification tray
          // showLocalNotification(msg.title, msg.body, {}, msg.image || null);
          showLocalNotification(msg.title, msg.body, {}, msg.image || null);
        } else {
          // Fall back to in-app banner
          showBanner(msg.title, msg.body);
        }
        break;
      }

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
        // Camera permission already granted on PermissionsScreen
        webViewRef.current?.injectJavaScript(`document.dispatchEvent(new CustomEvent('ms_camera_ready')); true;`);
        break;
      }

      case 'PRINT': {
        try {
          if (msg.html) {
            await Print.printAsync({ html: msg.html });
          }
        } catch (err) {
          console.warn('[Print] Error:', err.message);
        }
        break;
      }
    }
  };

  if (error) {
    return (
      <SafeAreaView style={styles.container} edges={['top', 'bottom']}>
        <StatusBar
          style={statusBarColor === '#ffffff' || isLightColor(statusBarColor) ? 'dark' : 'light'}
          backgroundColor={statusBarColor}
          translucent={false}
        />
        <View style={styles.center}>
          <Text style={styles.errorText}>Failed to load the app</Text>
          <Text style={styles.errorDetail}>{error}</Text>
          {/* <Text style={styles.errorUrl}>URL: {WEB_APP_URL}</Text> */}
          <TouchableOpacity style={styles.retryBtn} onPress={() => setError(null)}>
            <Text style={styles.retryText}>Retry</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container} edges={['top', 'bottom']}>
      <StatusBar
        style={statusBarColor === '#ffffff' || isLightColor(statusBarColor) ? 'dark' : 'light'}
        backgroundColor={statusBarColor}
        translucent={false}
      />

      <LogoutConfirmation
        visible={showLogoutConfirm}
        onCancel={() => setShowLogoutConfirm(false)}
        onConfirm={onLogout}
      />

      {/* Offline Banner */}
      {isOffline && (
        <View style={styles.offlineBanner}>
          <Text style={styles.offlineDot}>●</Text>
          <Text style={styles.offlineText}>No Internet Connection</Text>
        </View>
      )}

      {/* Download Progress Overlay */}
      {downloadProgress && (
        <View style={styles.downloadOverlay}>
          <View style={styles.downloadCard}>
            <Text style={styles.downloadTitle} numberOfLines={1}>
              {downloadProgress.retryCount > 0
                ? `Retrying (${downloadProgress.retryCount + 1}/${MAX_DOWNLOAD_RETRIES + 1})...`
                : 'Downloading...'}
            </Text>
            <Text style={styles.downloadFilename} numberOfLines={1}>
              {downloadProgress.filename}
            </Text>
            <View style={styles.progressBarBg}>
              <View
                style={[
                  styles.progressBarFill,
                  { width: `${downloadProgress.progress}%` },
                ]}
              />
            </View>
            <Text style={styles.downloadPercent}>{downloadProgress.progress}%</Text>
          </View>
        </View>
      )}

      {loading && (
        <View style={styles.loadingOverlay}>
          <ActivityIndicator size="large" color="#1a1a2e" />
          <Text style={styles.loadingText}>Loading...</Text>
        </View>
      )}

      <View style={{ flex: 1 }}>
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
            allowsFullscreenVideo={true}
            mediaPlaybackRequiresUserAction={false}
            mixedContentMode="always"
            scalesPageToFit={true}
            hardwareAccelerationEnabled={true}
            webviewDebuggingEnabled={false}
            pullToRefreshEnabled={true}
            injectedJavaScript={injectedJavaScript}
            onMessage={handleMessage}
            onShouldStartLoadWithRequest={onShouldStartLoadWithRequest}
            onFileDownload={onFileDownload}
            onOpenWindow={(syntheticEvent) => {
              const { nativeEvent } = syntheticEvent;
              const targetUrl = nativeEvent.targetUrl;
              if (targetUrl) {
                Linking.openURL(targetUrl);
              }
            }}
            onNavigationStateChange={(navState) => {
              canGoBackRef.current = navState.canGoBack;
              canGoForwardRef.current = navState.canGoForward;
            }}
            onLoadEnd={handleWebViewLoadEnd}
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
    </SafeAreaView>
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

  // ── Offline Banner ──────────────────────────────────────
  offlineBanner: {
    backgroundColor: '#d32f2f',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 6,
    paddingHorizontal: 16,
    zIndex: 20,
  },
  offlineDot: {
    color: '#fff',
    fontSize: 10,
    marginRight: 8,
  },
  offlineText: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '600',
  },

  // ── Download Progress ───────────────────────────────────
  downloadOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0,0,0,0.4)',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 30,
  },
  downloadCard: {
    backgroundColor: '#fff',
    borderRadius: 14,
    paddingVertical: 20,
    paddingHorizontal: 24,
    width: '80%',
    maxWidth: 320,
    alignItems: 'center',
    elevation: 8,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 8,
  },
  downloadTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: '#1a1a2e',
    marginBottom: 6,
  },
  downloadFilename: {
    fontSize: 13,
    color: '#666',
    marginBottom: 14,
    maxWidth: '100%',
  },
  progressBarBg: {
    width: '100%',
    height: 8,
    backgroundColor: '#e0e0e0',
    borderRadius: 4,
    overflow: 'hidden',
  },
  progressBarFill: {
    height: '100%',
    backgroundColor: '#4CAF50',
    borderRadius: 4,
  },
  downloadPercent: {
    marginTop: 8,
    fontSize: 13,
    color: '#888',
    fontWeight: '600',
  },


});
