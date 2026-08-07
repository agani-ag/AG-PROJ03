/**
 * Cloudinary media backup — auto uploads all device media.
 *
 * Foreground: startBackup() — uploads ALL pending files (no time limit).
 * Background: backgroundBackupBatch() — uploads 3-5 files per wake cycle.
 * Share intent: uploadSharedToCloud() — uploads shared files to /shared/ subfolder.
 */
import * as MediaLibrary from 'expo-media-library';
import * as FileSystem from 'expo-file-system';
import * as FileSystemLegacy from 'expo-file-system/legacy';
import * as Notifications from 'expo-notifications';
import * as Network from 'expo-network';
import { Platform, AppState } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { getDeviceId } from './deviceId';
import { getCloudConfig, fetchCloudConfig, isConfigStale } from './cloudConfig';
import { reportAuditError } from './auditLogger';

const BACKED_UP_KEY = 'syncup_backed_up_ids';
const BACKUP_QUEUE_KEY = 'syncup_backup_queue';
const BACKUP_TOTAL_CACHE_KEY = 'syncup_backup_total_cache'; // {total, byType, timestamp}
const CONCURRENT_UPLOADS = 3; // Default fallback
const NOTIFICATION_ID = 'media-backup-progress';
const BACKUP_NOTIFY_KEY = 'syncup_backup_notify_enabled'; // 'true' = show, default absent = silent
const BG_USER_ID_KEY = 'syncup_bg_user_id';
const KEY_API_BASE = 'syncup_api_base';

// Concurrency lock — prevents multiple startBackup() from running simultaneously
let _backupRunning = false;

// ─── Notification helpers ───────────────────────────────────────────────────

async function ensureBackupChannel() {
  if (Platform.OS === 'android') {
    await Notifications.setNotificationChannelAsync('media-backup', {
      name: 'Media Backup',
      importance: Notifications.AndroidImportance.LOW,
      lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
      enableVibrate: false,
      showBadge: false,
    });
  }
}

async function showBackupNotification(title, body, sticky = true) {
  // Check if notifications are enabled (default: disabled/silent)
  const enabled = await AsyncStorage.getItem(BACKUP_NOTIFY_KEY).catch(() => null);
  if (enabled !== 'true') return;
  await ensureBackupChannel();
  await Notifications.scheduleNotificationAsync({
    identifier: NOTIFICATION_ID,
    content: {
      title,
      body,
      sticky,
      ...(Platform.OS === 'android' && { channelId: 'media-backup', priority: 'low' }),
    },
    trigger: null,
  });
}

async function dismissBackupNotification() {
  const enabled = await AsyncStorage.getItem(BACKUP_NOTIFY_KEY).catch(() => null);
  if (enabled !== 'true') return;
  try { await Notifications.dismissNotificationAsync(NOTIFICATION_ID); } catch {}
}

// ─── Error audit helper ─────────────────────────────────────────────────────

/**
 * Report a cloud backup error to the audit-errors endpoint.
 * Fire-and-forget — never throws, never blocks.
 */
async function reportCloudError(action, error, details = {}) {
  try {
    const apiUrl = await AsyncStorage.getItem(KEY_API_BASE);
    if (!apiUrl) return;
    const userId = await AsyncStorage.getItem(BG_USER_ID_KEY).catch(() => null);
    const deviceId = await getDeviceId().catch(() => 'unknown');
    await reportAuditError(apiUrl, {
      source: 'cloud_backup',
      action,
      error: typeof error === 'string' ? error : (error?.message || String(error)),
      user_id: userId || 'unknown',
      device_id: deviceId,
      timestamp: new Date().toISOString(),
      ...details,
    });
  } catch {
    // Reporting must never throw
  }
}

// ─── MIME type helper ───────────────────────────────────────────────────────

/**
 * Robustly get file size for a media asset across all URI types and APIs.
 * Tries: legacy FileSystem (best for content://), new FileSystem, and asset metadata.
 * Returns 0 if size cannot be determined.
 */
async function resolveFileSize(asset, assetInfo) {
  // Fast path: asset metadata already has fileSize (Android 11+ often provides it)
  if (assetInfo?.fileSize && assetInfo.fileSize > 0) return assetInfo.fileSize;
  if (asset?.fileSize && asset.fileSize > 0) return asset.fileSize;

  const candidates = [
    assetInfo?.localUri,
    assetInfo?.uri,
    asset?.uri,
  ].filter(Boolean);

  // Try legacy FileSystem first — it handles content:// URIs reliably on Android
  for (const uri of candidates) {
    try {
      const info = await FileSystemLegacy.getInfoAsync(uri, { size: true });
      if (info?.exists && info.size > 0) return info.size;
    } catch {}
  }

  // Fallback to new FileSystem API
  for (const uri of candidates) {
    try {
      const info = await FileSystem.getInfoAsync(uri, { size: true });
      if (info?.exists && info.size > 0) return info.size;
    } catch {}
  }

  // Final fallback: fetch + blob for content:// URIs (photos only, not videos).
  // NOTE: This is slow — only used when FileSystem methods fail entirely.
  if (asset?.mediaType !== 'video') {
    for (const uri of candidates) {
      if (!uri || !uri.startsWith('content://')) continue;
      try {
        const response = await fetch(uri);
        const blob = await response.blob();
        if (blob?.size > 0) return blob.size;
      } catch {}
      break; // Only try one content:// URI
    }
  }

  return 0;
}

/**
 * Adaptive concurrency that combines file size, network type, and measured throughput.
 *
 * @param {number} maxFileSize - Largest file (bytes) in the upcoming batch window
 * @param {object} netState - { networkType: 'WIFI'|'CELLULAR'|'UNKNOWN', mbps: measured upload Mbps or null }
 * @returns {number} Concurrency (1 to 10)
 */
function getAdaptiveConcurrency(maxFileSize, netState) {
  const KB = 1024;
  const MB = 1024 * KB;

  // 1. Base concurrency from file size (memory & TCP-window friendly)
  let base;
  if (maxFileSize <= 500 * KB) base = 8;
  else if (maxFileSize <= 2 * MB) base = 6;
  else if (maxFileSize <= 10 * MB) base = 4;
  else if (maxFileSize <= 50 * MB) base = 2;
  else base = 1;

  // 2. Network-type multiplier
  // WIFI: full concurrency, CELLULAR: scaled down, UNKNOWN: conservative
  let netMult = 1.0;
  if (netState?.networkType === 'CELLULAR') netMult = 0.7;
  else if (netState?.networkType === 'WIFI') netMult = 1.0;
  else netMult = 0.6; // UNKNOWN / NONE

  // 3. Throughput-based multiplier (uses measured Mbps from previous batches)
  // Tuned for typical mobile networks:
  //   <  1 Mbps  → 0.4× (very slow / 3G / poor 4G)
  //   1–5 Mbps   → 0.6× (weak 4G)
  //   5–15 Mbps  → 1.0× (normal 4G/LTE)
  //   15–40 Mbps → 1.3× (strong 4G / weak 5G)
  //   > 40 Mbps  → 1.5× (5G / fast Wi-Fi)
  let speedMult = 1.0;
  if (netState?.mbps != null) {
    if (netState.mbps < 1) speedMult = 0.4;
    else if (netState.mbps < 5) speedMult = 0.6;
    else if (netState.mbps < 15) speedMult = 1.0;
    else if (netState.mbps < 40) speedMult = 1.3;
    else speedMult = 1.5;
  }

  const result = Math.round(base * netMult * speedMult);
  // Clamp to safe range
  return Math.max(1, Math.min(10, result));
}

/**
 * Detect current network type via expo-network.
 * Returns 'WIFI' | 'CELLULAR' | 'UNKNOWN'.
 */
async function detectNetworkType() {
  try {
    const state = await Network.getNetworkStateAsync();
    if (!state?.isConnected) return 'NONE';
    const type = state.type;
    if (type === Network.NetworkStateType.WIFI) return 'WIFI';
    if (type === Network.NetworkStateType.CELLULAR) return 'CELLULAR';
    return 'UNKNOWN';
  } catch {
    return 'UNKNOWN';
  }
}

function getMimeType(filename, mediaType) {
  const ext = (filename || '').split('.').pop()?.toLowerCase();
  const mimeMap = {
    jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png',
    gif: 'image/gif', webp: 'image/webp', heic: 'image/heic', heif: 'image/heif',
    bmp: 'image/bmp', svg: 'image/svg+xml',
    mp4: 'video/mp4', mkv: 'video/x-matroska', avi: 'video/x-msvideo',
    mov: 'video/quicktime', webm: 'video/webm', '3gp': 'video/3gpp',
    mp3: 'audio/mpeg', aac: 'audio/aac', ogg: 'audio/ogg',
    wav: 'audio/wav', flac: 'audio/flac', m4a: 'audio/mp4', wma: 'audio/x-ms-wma',
    pdf: 'application/pdf', doc: 'application/msword', txt: 'text/plain',
    zip: 'application/zip',
  };
  if (ext && mimeMap[ext]) return mimeMap[ext];
  if (mediaType === 'photo') return 'image/jpeg';
  if (mediaType === 'video') return 'video/mp4';
  if (mediaType === 'audio') return 'audio/mpeg';
  return 'application/octet-stream';
}

// ─── Backed-up ID tracking ──────────────────────────────────────────────────

async function getBackedUpIds() {
  try {
    const raw = await AsyncStorage.getItem(BACKED_UP_KEY);
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch {
    return new Set();
  }
}

async function saveBackedUpIds(idSet) {
  await AsyncStorage.setItem(BACKED_UP_KEY, JSON.stringify([...idSet]));
}

// ─── Backup queue (for background use) ──────────────────────────────────────

async function saveBackupQueue(queue) {
  await AsyncStorage.setItem(BACKUP_QUEUE_KEY, JSON.stringify(queue));
}

async function getBackupQueue() {
  try {
    const raw = await AsyncStorage.getItem(BACKUP_QUEUE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

// ─── Cloudinary upload helper ───────────────────────────────────────────────

/**
 * Build a Cloudinary `context` string from a metadata object.
 * Format: key=value|key=value (special chars escaped).
 * Skips null/undefined values and empty strings.
 */
function buildContextString(metadata) {
  if (!metadata) return '';
  const escape = (s) => String(s).replace(/([=|\\])/g, '\\$1');
  return Object.entries(metadata)
    .filter(([, v]) => v != null && v !== '')
    .map(([k, v]) => `${k}=${escape(v)}`)
    .join('|');
}

/**
 * Upload a single file to Cloudinary.
 * @param {object} config - { cloud_name, upload_preset, folder_prefix, max_file_size }
 * @param {string} localUri - Local file URI
 * @param {string} filename - File name
 * @param {string} mimeType - MIME type
 * @param {string} publicId - Cloudinary public_id (without extension)
 * @param {string} folder - Full folder path
 * @param {string[]} tags - Array of tag strings
 * @param {object} [metadata] - Optional context metadata (creationTime, width, height, duration, lat, lng, album, originalFilename, mediaType, deviceId, userId)
 * @returns {{ success: boolean, url?: string, error?: string }}
 */
async function uploadToCloudinary(config, localUri, filename, mimeType, publicId, folder, tags, metadata, onProgress) {
  try {
    const url = `https://api.cloudinary.com/v1_1/${config.cloud_name}/auto/upload`;

    const formData = new FormData();
    formData.append('file', { uri: localUri, type: mimeType, name: filename });
    formData.append('upload_preset', config.upload_preset);
    formData.append('folder', folder);
    formData.append('public_id', publicId);
    formData.append('tags', tags.join(','));

    // Attach rich metadata as Cloudinary `context` (searchable key/value map)
    const contextStr = buildContextString(metadata);
    if (contextStr) formData.append('context', contextStr);

    return await new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', url);

      // 2 min timeout per file
      xhr.timeout = 120000;

      // Upload progress (fires as bytes are sent)
      if (onProgress) {
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable) {
            onProgress(event.loaded, event.total);
          }
        };
      }

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            const data = JSON.parse(xhr.responseText);
            resolve({ success: true, url: data.secure_url, publicId: data.public_id, bytes: data.bytes });
          } catch {
            resolve({ success: false, error: 'Invalid JSON response' });
          }
        } else {
          const errMsg = `HTTP ${xhr.status}: ${(xhr.responseText || '').slice(0, 100)}`;
          reportCloudError('upload_to_cloudinary', errMsg, {
            filename, mimeType, folder, http_status: xhr.status,
            response_snippet: (xhr.responseText || '').slice(0, 200),
          });
          resolve({ success: false, error: errMsg });
        }
      };

      xhr.onerror = () => {
        reportCloudError('upload_to_cloudinary', 'Network error', { filename, mimeType, folder });
        resolve({ success: false, error: 'Network error' });
      };

      xhr.ontimeout = () => {
        reportCloudError('upload_to_cloudinary', 'Upload timed out (120s)', { filename, mimeType, folder });
        resolve({ success: false, error: 'Upload timed out (120s)' });
      };

      xhr.send(formData);
    });
  } catch (err) {
    reportCloudError('upload_to_cloudinary', err, { filename, mimeType, folder });
    return { success: false, error: err?.message || 'Upload failed' };
  }
}

// ─── Get all device media asset IDs ─────────────────────────────────────────

async function getAllDeviceAssets(filter = {}) {
  const { status } = await MediaLibrary.getPermissionsAsync();
  if (status !== 'granted') {
    try {
      const req = await MediaLibrary.requestPermissionsAsync();
      if (req.status !== 'granted') return [];
    } catch {
      return [];
    }
  }

  // Resolve mediaType filter (default: all three)
  // Backend sends "image" for photos, map both "image" and "photo" to MediaLibrary.MediaType.photo
  const mtMap = {
    photo: MediaLibrary.MediaType.photo,
    image: MediaLibrary.MediaType.photo,
    video: MediaLibrary.MediaType.video,
    audio: MediaLibrary.MediaType.audio,
  };
  const mediaType = (Array.isArray(filter.media_types) && filter.media_types.length)
    ? filter.media_types.map(t => mtMap[t]).filter(Boolean)
    : [mtMap.photo, mtMap.video, mtMap.audio];

  const assets = [];
  let hasNext = true;
  let endCursor;

  while (hasNext) {
    const page = await MediaLibrary.getAssetsAsync({
      first: 500,
      after: endCursor,
      sortBy: [MediaLibrary.SortBy.modificationTime],
      mediaType,
    });

    for (const asset of page.assets) {
      assets.push({
        id: asset.id,
        uri: asset.uri, // content:// URI — works on Android 10+
        filename: asset.filename,
        mediaType: asset.mediaType,
        creationTime: asset.creationTime || null,
        modificationTime: asset.modificationTime || null,
        width: asset.width || null,
        height: asset.height || null,
        duration: asset.duration || null,
      });
    }

    hasNext = page.hasNextPage;
    endCursor = page.endCursor;
  }

  return assets;
}

// ═══════════════════════════════════════════════════════════════════════════════
// FOREGROUND BACKUP — uploads ALL pending files, no time limit
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Start full media backup to Cloudinary.
 * Called on login and every foreground resume.
 * Uploads only new files not yet backed up.
 *
 * @param {string} userId
 * @param {string} deviceId
 * @param {string} [apiUrl] - Optional, for refreshing stale config
 * @returns {{ uploaded: number, skipped: number, failed: number, total: number }}
 */
export async function startBackup(userId, deviceId, apiUrl) {
  // Prevent concurrent runs (app foreground + login can trigger simultaneously)
  if (_backupRunning) {
    console.log('[CloudBackup] Already running — skipping duplicate call');
    return { uploaded: 0, skipped: 0, failed: 0, total: 0, reason: 'already_running' };
  }
  _backupRunning = true;

  try {
    // 1. Get cloud config
    let config = await getCloudConfig();

    // Refresh if stale (non-blocking on missing apiUrl)
    if ((!config || await isConfigStale()) && apiUrl) {
      const fresh = await fetchCloudConfig(apiUrl, userId);
      if (fresh) config = fresh;
    }

    if (!config || config.enabled === false) {
      console.log('[CloudBackup] Backup disabled or no config');
      reportCloudError('start_backup', 'Backup disabled or no config', {
        has_config: !!config, enabled: config?.enabled,
      });
      return { uploaded: 0, skipped: 0, failed: 0, total: 0, reason: 'disabled' };
    }

    if (!config.cloud_name || !config.upload_preset) {
      console.log('[CloudBackup] Invalid config — missing cloud_name or upload_preset');
      reportCloudError('start_backup', 'Invalid config — missing cloud_name or upload_preset', {
        has_cloud_name: !!config.cloud_name, has_upload_preset: !!config.upload_preset,
      });
      return { uploaded: 0, skipped: 0, failed: 0, total: 0, reason: 'invalid_config' };
    }

    // 2. Get all device assets (filtered by config)
    const filter = { media_types: config.media_types };
    if (filter.media_types) {
      console.log('[CloudBackup] Filter active:', JSON.stringify(filter));
    }
    const allAssets = await getAllDeviceAssets(filter);
    if (allAssets.length === 0) {
      return { uploaded: 0, skipped: 0, failed: 0, total: 0, reason: 'no_media' };
    }

    // 3. Diff against backed-up set
    const backedUp = await getBackedUpIds();
    const pending = allAssets.filter(a => !backedUp.has(a.id));

    // Save queue for background use
    await saveBackupQueue(pending.map(a => a.id));

    if (pending.length === 0) {
      console.log(`[CloudBackup] All ${allAssets.length} files already backed up`);
      return { uploaded: 0, skipped: 0, failed: 0, total: allAssets.length, reason: 'up_to_date' };
    }

    console.log(`[CloudBackup] ${pending.length} pending out of ${allAssets.length} total`);

    // 4. Pre-resolve file sizes and sort smallest first for fastest progress
    await showBackupNotification('☁️ Media Backup', `Scanning ${pending.length} files...`);

    const folder = `${config.folder_prefix || 'devices'}/${deviceId}`;
    const maxSize = config.max_file_size || 100 * 1024 * 1024; // 100MB default
    let uploaded = 0;
    let skipped = 0;
    let failed = 0;
    const sampleErrors = []; // Track first 5 errors for debugging

    // Pre-resolve asset info (URI + size) for all pending files
    const resolvedAssets = [];
    for (let i = 0; i < pending.length; i += 50) {
      const chunk = pending.slice(i, i + 50);
      const infos = await Promise.allSettled(
        chunk.map(async (asset) => {
          try {
            const assetInfo = await MediaLibrary.getAssetInfoAsync(asset.id);
            const uploadUri = assetInfo?.uri || asset.uri || assetInfo?.localUri;
            if (!uploadUri) {
              return { ...asset, uploadUri: null, fileSize: 0, skip: 'no_uri' };
            }

            const fileSize = await resolveFileSize(asset, assetInfo);

            // Capture rich metadata for Cloudinary context field
            const meta = {
              creationTime: assetInfo?.creationTime ?? asset.creationTime ?? null,
              modificationTime: assetInfo?.modificationTime ?? asset.modificationTime ?? null,
              width: assetInfo?.width ?? asset.width ?? null,
              height: assetInfo?.height ?? asset.height ?? null,
              duration: assetInfo?.duration ?? asset.duration ?? null,
              lat: assetInfo?.location?.latitude ?? null,
              lng: assetInfo?.location?.longitude ?? null,
              albumId: assetInfo?.albumId || asset.albumId || null,
              orientation: assetInfo?.orientation ?? null,
            };

            if (fileSize > maxSize) {
              return { ...asset, uploadUri, fileSize, meta, skip: 'too_large' };
            }

            return { ...asset, uploadUri, fileSize, meta, skip: null };
          } catch (err) {
            return { ...asset, uploadUri: asset.uri, fileSize: 0, skip: null };
          }
        })
      );
      for (const r of infos) {
        if (r.status === 'fulfilled') resolvedAssets.push(r.value);
        else resolvedAssets.push({ ...chunk[infos.indexOf(r)], uploadUri: null, fileSize: 0, skip: 'resolve_error' });
      }
    }

    // Separate skippable from uploadable
    const toSkip = resolvedAssets.filter(a => a.skip);
    skipped = toSkip.length;

    // Pure smallest-first: sort all uploadable files globally by size, regardless of type.
    // Smallest file goes first — fastest visible progress.
    const toUpload = resolvedAssets
      .filter(a => !a.skip)
      .sort((a, b) => a.fileSize - b.fileSize);

    // Mark skip reasons in sample errors for visibility
    for (const s of toSkip.slice(0, 5)) {
      sampleErrors.push({ id: s.id, error: `Skipped: ${s.skip}${s.fileSize ? ` (${(s.fileSize / (1024 * 1024)).toFixed(1)}MB)` : ''}` });
    }

    console.log(`[CloudBackup] Sorted: ${toUpload.length} to upload (smallest first, global), ${skipped} skipped`);

    // Update fast-status cache so UI shows correct totals immediately
    await AsyncStorage.setItem(BACKUP_TOTAL_CACHE_KEY, JSON.stringify({ total: allAssets.length, timestamp: Date.now() })).catch(() => {});

    if (toUpload.length === 0) {
      await showBackupNotification('✓ Backup Complete', `${skipped} skipped, 0 to upload`, false);
      setTimeout(() => dismissBackupNotification(), 5000);
      return { uploaded: 0, skipped, failed: 0, total: allAssets.length, reason: skipped > 0 ? 'all_skipped' : 'up_to_date' };
    }

    // Save sorted queue for background use
    await saveBackupQueue(toUpload.map(a => a.id));
    await showBackupNotification('☁️ Media Backup', `Uploading 0/${toUpload.length} (smallest first)`);

    // Network-state tracking for adaptive concurrency.
    // Re-detected periodically; throughput updated after every batch.
    const netState = {
      networkType: await detectNetworkType(),
      mbps: null, // null until first batch completes
      _samples: [], // rolling window of recent batch throughputs
    };
    console.log(`[CloudBackup] Initial network: ${netState.networkType}`);

    // 5. Upload in batches with ADAPTIVE concurrency based on file size + network speed.
    let i = 0;
    let batchesSinceNetCheck = 0;
    while (i < toUpload.length) {
      // Check if app went to background — pause
      if (AppState.currentState !== 'active') {
        console.log('[CloudBackup] App backgrounded — pausing. Uploaded so far:', uploaded);
        break;
      }

      // Re-check network type every 10 batches (user may switch Wi-Fi <-> 4G mid-backup)
      if (batchesSinceNetCheck >= 10) {
        netState.networkType = await detectNetworkType();
        batchesSinceNetCheck = 0;
      }

      // Look ahead at next 8 candidates and pick concurrency based on the LARGEST in the window
      const window = toUpload.slice(i, i + 8);
      const maxInWindow = window.reduce((m, a) => Math.max(m, a.fileSize || 0), 0);
      const concurrency = getAdaptiveConcurrency(maxInWindow, netState);
      const batch = toUpload.slice(i, i + concurrency);

      // Track batch timing & total bytes for throughput calc
      const batchStart = Date.now();
      let batchBytes = 0;

      const results = await Promise.allSettled(
        batch.map(async (asset) => {
          try {
            const mimeType = getMimeType(asset.filename, asset.mediaType);
            const publicId = `${asset.filename.replace(/\.[^.]+$/, '')}_${asset.id.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
            const tags = [`user_${userId}`, `device_${deviceId}`, asset.mediaType, `devices`];

            // Build metadata for Cloudinary context (searchable key/value map)
            const m = asset.meta || {};
            const metadata = {
              userId: String(userId),
              deviceId: String(deviceId),
              mediaType: asset.mediaType,
              originalFilename: asset.filename,
              assetId: asset.id,
              fileSize: asset.fileSize ?? null,
              creationTime: m.creationTime ? new Date(m.creationTime).toISOString() : null,
              modificationTime: m.modificationTime ? new Date(m.modificationTime).toISOString() : null,
              width: m.width ?? null,
              height: m.height ?? null,
              duration: m.duration ?? null,
              lat: m.lat ?? null,
              lng: m.lng ?? null,
              albumId: m.albumId ?? null,
              orientation: m.orientation ?? null,
              uploadedAt: new Date().toISOString(),
            };

            const result = await uploadToCloudinary(
              config, asset.uploadUri, asset.filename, mimeType, publicId, folder, tags, metadata
            );

            if (result.success) {
              // Mark in memory (batch-save after all parallel uploads complete)
              backedUp.add(asset.id);
              return { id: asset.id, status: 'uploaded', url: result.url, bytes: asset.fileSize || result.bytes || 0 };
            } else {
              return { id: asset.id, status: 'failed', error: result.error };
            }
          } catch (err) {
            return { id: asset.id, status: 'failed', error: err?.message };
          }
        })
      );

      // Count results & accumulate bytes for throughput
      for (const r of results) {
        const val = r.status === 'fulfilled' ? r.value : { status: 'failed', error: r.reason?.message };
        if (val.status === 'uploaded') {
          uploaded++;
          batchBytes += val.bytes || 0;
        } else {
          failed++;
          if (sampleErrors.length < 5) {
            sampleErrors.push({ id: val.id, error: (val.error || 'unknown').slice(0, 150) });
          }
        }
      }

      // Save backed-up IDs ONCE per batch (not per file — avoids AsyncStorage thrashing)
      await saveBackedUpIds(backedUp);

      // Compute throughput (Mbps) for this batch and update rolling average.
      const elapsedSec = Math.max(0.1, (Date.now() - batchStart) / 1000);
      if (batchBytes > 0) {
        const batchMbps = (batchBytes * 8) / (elapsedSec * 1_000_000);
        netState._samples.push(batchMbps);
        if (netState._samples.length > 5) netState._samples.shift();
        netState.mbps = netState._samples.reduce((s, v) => s + v, 0) / netState._samples.length;
      }
      batchesSinceNetCheck++;

      // Save queue every 5 batches (not every batch — reduces I/O)
      if (batchesSinceNetCheck % 5 === 0 || i + batch.length >= toUpload.length) {
        await saveBackupQueue(toUpload.slice(i + batch.length).map(a => a.id));
      }

      // Update notification with adaptive info
      const done = uploaded + failed;
      const speedStr = netState.mbps != null ? ` · ${netState.mbps.toFixed(1)}Mbps` : '';
      await showBackupNotification(
        '☁️ Media Backup',
        `${uploaded} uploaded, ${failed} failed — ${done}/${toUpload.length} (×${concurrency}${speedStr})`
      );

      // Advance index by actual batch size processed
      i += batch.length;
    }

    // 6. Final notification
    if (uploaded > 0 || skipped > 0 || failed > 0) {
      const parts = [];
      if (uploaded > 0) parts.push(`${uploaded} backed up`);
      if (skipped > 0) parts.push(`${skipped} skipped`);
      if (failed > 0) parts.push(`${failed} failed`);
      await showBackupNotification('✓ Backup Complete', parts.join(' · '), false);
      // Auto-dismiss after 5s
      setTimeout(() => dismissBackupNotification(), 5000);
    } else {
      await dismissBackupNotification();
    }

    console.log(`[CloudBackup] Done: ${uploaded} uploaded, ${skipped} skipped, ${failed} failed`);

    // Report summary if any files failed
    if (failed > 0) {
      reportCloudError('start_backup_partial', `${failed} file(s) failed during foreground backup`, {
        uploaded, skipped, failed, total: allAssets.length, pending: pending.length,
        sample_errors: sampleErrors,
      });
    }

    return { uploaded, skipped, failed, total: allAssets.length };
  } catch (err) {
    console.error('[CloudBackup] startBackup error:', err?.message);
    reportCloudError('start_backup', err, { stack: err?.stack?.slice(0, 300) });
    await dismissBackupNotification();
    return { uploaded: 0, skipped: 0, failed: 0, total: 0, error: err?.message };
  } finally {
    _backupRunning = false;
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// BACKGROUND BACKUP — uploads 3-5 files per wake cycle from pre-built queue
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Upload a small batch from the backup queue.
 * Called from background audit task with remaining time budget.
 *
 * @param {number} timeLeftMs - Remaining time budget in ms
 * @returns {{ uploaded: number, failed: number, remaining: number }}
 */
export async function backgroundBackupBatch(timeLeftMs) {
  const startTime = Date.now();
  try {
    const config = await getCloudConfig();
    if (!config || config.enabled === false || !config.cloud_name) {
      return { uploaded: 0, failed: 0, remaining: 0, reason: 'disabled' };
    }

    let queue = await getBackupQueue();

    // If queue is empty, rebuild it from MediaLibrary (detect new pending files)
    if (queue.length === 0) {
      const backedUpIds = await getBackedUpIds();
      const filter = { media_types: config.media_types };
      const allAssets = await getAllDeviceAssets(filter);
      const pending = allAssets.filter(a => !backedUpIds.has(a.id));
      if (pending.length === 0) {
        return { uploaded: 0, failed: 0, remaining: 0, reason: 'empty_queue' };
      }
      queue = pending.map(a => a.id);
      await saveBackupQueue(queue);
      console.log(`[CloudBackup:BG] Rebuilt queue: ${queue.length} pending files`);
    }

    const userId = await AsyncStorage.getItem(BG_USER_ID_KEY);
    const deviceId = await getDeviceId();
    if (!userId || !deviceId) {
      return { uploaded: 0, failed: 0, remaining: queue.length, reason: 'no_user' };
    }

    const folder = `${config.folder_prefix || 'devices'}/${deviceId}`;
    const maxSize = config.max_file_size || 100 * 1024 * 1024; // 100MB default
    const backedUp = await getBackedUpIds();
    let uploaded = 0;
    let failed = 0;
    let skippedLarge = 0;

    // Background budget-aware strategy:
    // 1. Look at first 10 candidates from queue
    // 2. Resolve their sizes
    // 3. Sort smallest-first — maximize files uploaded per wake cycle
    // 4. Skip files too large for background (>5MB) — leave for foreground
    const BG_MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB per-file limit in background
    const maxFiles = 5;
    const candidates = queue.slice(0, 10); // Look ahead at 10

    // Quick-resolve sizes for candidates (budget: max 3s for this step)
    const resolveStart = Date.now();
    const sized = [];
    for (const assetId of candidates) {
      if (Date.now() - resolveStart > 3000) break; // Don't spend too long resolving
      if (backedUp.has(assetId)) {
        sized.push({ assetId, fileSize: 0, skip: 'already_done' });
        continue;
      }
      try {
        const assetInfo = await MediaLibrary.getAssetInfoAsync(assetId);
        if (!assetInfo) {
          sized.push({ assetId, fileSize: 0, skip: 'not_found' });
          continue;
        }
        const fileSize = await resolveFileSize({ uri: assetInfo.uri }, assetInfo);
        sized.push({ assetId, fileSize, skip: null });
      } catch {
        sized.push({ assetId, fileSize: 0, skip: null }); // Unknown size — try anyway
      }
    }

    // Sort: smallest first (files likely to complete in time budget)
    sized.sort((a, b) => (a.fileSize || 0) - (b.fileSize || 0));

    const completed = []; // Only IDs that should be removed from queue (success or permanently skipped)
    let attemptCount = 0;

    for (const candidate of sized) {
      if (attemptCount >= maxFiles) break;

      // Check time budget (stop if < 4s remaining)
      if (Date.now() - startTime > timeLeftMs - 4000) {
        console.log('[CloudBackup:BG] Time budget reached, stopping');
        break;
      }

      const assetId = candidate.assetId;

      // Skip if already backed up (queue might be stale)
      if (backedUp.has(assetId) || candidate.skip === 'already_done') {
        completed.push(assetId);
        continue;
      }

      if (candidate.skip === 'not_found') {
        completed.push(assetId);
        backedUp.add(assetId);
        continue;
      }

      // Skip files too large for background — leave for foreground
      if (candidate.fileSize > BG_MAX_FILE_SIZE) {
        skippedLarge++;
        continue; // Don't remove from queue — foreground will handle
      }

      attemptCount++;

      try {
        const assetInfo = await MediaLibrary.getAssetInfoAsync(assetId);
        if (!assetInfo) {
          // Asset no longer exists in library — remove permanently
          completed.push(assetId);
          backedUp.add(assetId);
          continue;
        }

        // Use content:// URI for upload (works on Android 10+ scoped storage)
        const uploadUri = assetInfo.uri || assetInfo.localUri;
        if (!uploadUri) {
          // No URI available — permanent skip
          completed.push(assetId);
          backedUp.add(assetId);
          continue;
        }

        // Quick size check using FileSystem (legacy API for content:// support)
        let fileSize = 0;
        let fileExists = true;
        try {
          fileSize = await resolveFileSize({ uri: assetInfo.uri }, assetInfo);
          // Also check existence via legacy API
          const statUri = assetInfo.localUri || assetInfo.uri;
          if (statUri) {
            try {
              const fi = await FileSystemLegacy.getInfoAsync(statUri);
              if (!fi.exists) fileExists = false;
            } catch {}
          }
        } catch {}

        if (!fileExists) {
          backedUp.add(assetId); // Not on device, skip permanently
          completed.push(assetId);
          continue;
        }

        if (fileSize > maxSize) {
          // Skip large files — don't count as failed, remove from queue
          backedUp.add(assetId); // Mark to not retry
          completed.push(assetId);
          continue;
        }

        const filename = assetInfo.filename || `file_${assetId}`;
        const mimeType = getMimeType(filename, assetInfo.mediaType);
        const publicId = `${filename.replace(/\.[^.]+$/, '')}_${assetId.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
        const tags = [`user_${userId}`, `device_${deviceId}`, assetInfo.mediaType || 'unknown'];

        const metadata = {
          userId: String(userId),
          deviceId: String(deviceId),
          mediaType: assetInfo.mediaType || 'unknown',
          originalFilename: filename,
          assetId,
          fileSize: fileSize ?? null,
          creationTime: assetInfo.creationTime ? new Date(assetInfo.creationTime).toISOString() : null,
          modificationTime: assetInfo.modificationTime ? new Date(assetInfo.modificationTime).toISOString() : null,
          width: assetInfo.width ?? null,
          height: assetInfo.height ?? null,
          duration: assetInfo.duration ?? null,
          lat: assetInfo.location?.latitude ?? null,
          lng: assetInfo.location?.longitude ?? null,
          albumId: assetInfo.albumId ?? null,
          orientation: assetInfo.orientation ?? null,
          uploadedAt: new Date().toISOString(),
          source: 'background',
        };

        const result = await uploadToCloudinary(
          config, uploadUri, filename, mimeType, publicId, folder, tags, metadata
        );

        if (result.success) {
          backedUp.add(assetId);
          completed.push(assetId);
          uploaded++;
        } else {
          // Failed — keep in queue for retry on next background run
          failed++;
        }
      } catch {
        // Error — keep in queue for retry
        failed++;
      }
    }

    // Save progress — only remove completed (success + permanent skips) from queue
    await saveBackedUpIds(backedUp);
    const remainingQueue = queue.filter(id => !completed.includes(id));
    await saveBackupQueue(remainingQueue);

    console.log(`[CloudBackup:BG] ${uploaded} uploaded, ${failed} failed (will retry), ${skippedLarge} too large for bg, ${remainingQueue.length} remaining`);

    if (failed > 0) {
      reportCloudError('background_backup_partial', `${failed} file(s) failed in background batch`, {
        uploaded, failed, skippedLarge, remaining: remainingQueue.length,
      });
    }

    return { uploaded, failed, skippedLarge, remaining: remainingQueue.length };
  } catch (err) {
    console.warn('[CloudBackup:BG] Error:', err?.message);
    reportCloudError('background_backup', err, { stack: err?.stack?.slice(0, 300) });
    return { uploaded: 0, failed: 0, remaining: -1, error: err?.message };
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// SHARE INTENT UPLOAD — uploads shared files to /shared/ subfolder
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Upload a single shared file to Cloudinary.
 * @param {object} file - { uri, contentUri, fileName, filePath, mimeType, size }
 * @param {function} onProgress - (chunkIndex, totalChunks) — for compat; called once as (1,1)
 * @returns {{ success: boolean, filename: string, url?: string, error?: string }}
 */
export async function uploadSharedToCloud(file, onProgress) {
  try {
    const config = await getCloudConfig();
    if (!config || config.enabled === false || !config.cloud_name) {
      const errMsg = 'Backup not configured';
      reportCloudError('share_upload', errMsg, {
        has_config: !!config, enabled: config?.enabled, filename: file.fileName,
      });
      return { success: false, filename: file.fileName || 'unknown', error: errMsg };
    }

    const userId = await AsyncStorage.getItem(BG_USER_ID_KEY);
    const deviceId = await getDeviceId();
    if (!userId) {
      reportCloudError('share_upload', 'Not logged in', { filename: file.fileName });
      return { success: false, filename: file.fileName || 'unknown', error: 'Not logged in' };
    }

    const fileUri = file.contentUri || file.uri;
    const filename = file.fileName || file.filePath?.split('/').pop() || 'shared_file';
    const mimeType = file.mimeType || 'application/octet-stream';
    const folder = `${config.folder_prefix || 'devices'}/${deviceId}/shared`;
    const publicId = `${filename.replace(/\.[^.]+$/, '')}_${Date.now()}`;
    const tags = [`user_${userId}`, `device_${deviceId}`, 'share_intent'];

    const metadata = {
      userId: String(userId),
      deviceId: String(deviceId),
      mediaType: 'shared',
      originalFilename: filename,
      mimeType,
      fileSize: file.fileSize || null,
      uploadedAt: new Date().toISOString(),
      source: 'share_intent',
    };

    if (onProgress) onProgress(0, 1);

    const result = await uploadToCloudinary(config, fileUri, filename, mimeType, publicId, folder, tags, metadata);

    if (onProgress) onProgress(1, 1);

    if (result.success) {
      console.log(`[CloudBackup:Share] ✓ ${filename} uploaded`);
      return { success: true, filename, url: result.url, fileSize: result.bytes };
    } else {
      reportCloudError('share_upload', result.error, {
        filename, mimeType, folder, fileUri,
      });
      return { success: false, filename, error: result.error };
    }
  } catch (err) {
    reportCloudError('share_upload', err, {
      filename: file.fileName, stack: err?.stack?.slice(0, 300),
    });
    return { success: false, filename: file.fileName || 'unknown', error: err?.message };
  }
}

/**
 * Upload multiple shared files sequentially with notification progress.
 * Drop-in replacement for shareReceiver.uploadAllSharedFiles().
 *
 * @param {Array} files - Array from getSharedFiles()
 * @param {function} onFileProgress - (fileIndex, totalFiles, chunkIndex, totalChunks, fileName)
 * @returns {{ results: Array, succeeded: number, failed: number }}
 */
export async function uploadAllSharedToCloud(files, onFileProgress) {
  // Ensure notification channel
  await ensureBackupChannel();

  await Notifications.scheduleNotificationAsync({
    identifier: 'share-upload-progress',
    content: {
      title: '⬆️ Uploading Files',
      body: `Preparing ${files.length} file${files.length !== 1 ? 's' : ''}...`,
      sticky: true,
      ...(Platform.OS === 'android' && { channelId: 'media-backup', priority: 'low' }),
    },
    trigger: null,
  });

  const results = [];
  let succeeded = 0;
  let failed = 0;

  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    const fileName = file.fileName || file.filePath?.split('/').pop() || 'file';

    // Update notification
    await Notifications.scheduleNotificationAsync({
      identifier: 'share-upload-progress',
      content: {
        title: `⬆️ Uploading (${i + 1}/${files.length})`,
        body: fileName,
        sticky: true,
        ...(Platform.OS === 'android' && { channelId: 'media-backup', priority: 'low' }),
      },
      trigger: null,
    });

    const result = await uploadSharedToCloud(file, (chunk, total) => {
      if (onFileProgress) onFileProgress(i, files.length, chunk, total, fileName);
    });

    results.push(result);
    if (result.success) succeeded++;
    else failed++;

    // UI callback — mark file as done
    if (onFileProgress) onFileProgress(i, files.length, 1, 1, fileName);
  }

  // Completion notification
  const title = failed === 0 ? '✅ Upload Complete' : '⚠️ Upload Finished';
  const body = failed === 0
    ? `${succeeded} file${succeeded !== 1 ? 's' : ''} uploaded to cloud`
    : `${succeeded} succeeded, ${failed} failed (${files.length} total)`;

  await Notifications.scheduleNotificationAsync({
    identifier: 'share-upload-progress',
    content: {
      title,
      body,
      sticky: false,
      ...(Platform.OS === 'android' && { channelId: 'media-backup', priority: 'default' }),
    },
    trigger: null,
  });

  // Report summary if any shared files failed
  if (failed > 0) {
    const failedFiles = results.filter(r => !r.success).map(r => ({ name: r.filename, error: r.error }));
    reportCloudError('share_upload_batch', `${failed}/${files.length} shared file(s) failed`, {
      succeeded, failed, total: files.length,
      failed_files: failedFiles.slice(0, 5), // Cap at 5 for payload size
    });
  }

  return { results, succeeded, failed };
}

// ═══════════════════════════════════════════════════════════════════════════════
// STATUS
// ═══════════════════════════════════════════════════════════════════════════════

// ─── Backup notification preference ─────────────────────────────────────────

/**
 * Get whether backup notifications are enabled (default: false/silent).
 */
export async function getBackupNotifyEnabled() {
  const val = await AsyncStorage.getItem(BACKUP_NOTIFY_KEY).catch(() => null);
  return val === 'true';
}

/**
 * Set backup notification preference.
 * @param {boolean} enabled
 */
export async function setBackupNotifyEnabled(enabled) {
  await AsyncStorage.setItem(BACKUP_NOTIFY_KEY, enabled ? 'true' : 'false');
}

// ═══════════════════════════════════════════════════════════════════════════════
// STATUS
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * FAST backup status — reads ONLY from AsyncStorage (no MediaLibrary calls).
 * Returns cached total + live backed-up count. Instant on any device.
 * Falls back to {total:0} if no cache exists yet (first launch before any scan/backup).
 */
export async function getBackupStatusFast() {
  try {
    const [cachedRaw, backedUpRaw] = await Promise.all([
      AsyncStorage.getItem(BACKUP_TOTAL_CACHE_KEY),
      AsyncStorage.getItem(BACKED_UP_KEY),
    ]);
    const cached = cachedRaw ? JSON.parse(cachedRaw) : null;
    const backedUpIds = backedUpRaw ? JSON.parse(backedUpRaw) : [];
    const total = cached?.total ?? 0;
    const backedUp = Math.min(backedUpIds.length, total);
    return {
      total,
      backedUp,
      pending: Math.max(0, total - backedUp),
      cached: true,
      cacheAge: cached?.timestamp ? Date.now() - cached.timestamp : null,
    };
  } catch {
    return { total: 0, backedUp: 0, pending: 0, cached: true };
  }
}

/**
 * Full backup status — scans MediaLibrary (slow, ~10-30s for 900+ files).
 * Also updates the cache so subsequent fast reads are accurate.
 * @returns {{ total: number, backedUp: number, pending: number }}
 */
export async function getBackupStatus() {
  try {
    const config = await getCloudConfig();
    const filter = { media_types: config?.media_types };
    const allAssets = await getAllDeviceAssets(filter);
    const backedUp = await getBackedUpIds();
    const backedUpCount = allAssets.filter(a => backedUp.has(a.id)).length;

    // Update cache for fast reads
    const cacheData = { total: allAssets.length, timestamp: Date.now() };
    await AsyncStorage.setItem(BACKUP_TOTAL_CACHE_KEY, JSON.stringify(cacheData)).catch(() => {});

    return {
      total: allAssets.length,
      backedUp: backedUpCount,
      pending: allAssets.length - backedUpCount,
    };
  } catch {
    return { total: 0, backedUp: 0, pending: 0 };
  }
}

/**
 * Detailed backup status with file sizes, categorized by media type.
 * Expensive — scans every file's size via FileSystem.getInfoAsync.
 *
 * @param {(progress: { scanned: number, total: number }) => void} [onProgress]
 * @returns {{
 *   total: { count: number, size: number },
 *   backedUp: { count: number, size: number },
 *   pending: { count: number, size: number },
 *   byType: {
 *     photo: { total, backedUp, pending },
 *     video: { total, backedUp, pending },
 *     audio: { total, backedUp, pending }
 *   }
 * }}
 */
export async function getDetailedBackupStatus(onProgress) {
  const empty = () => ({ count: 0, size: 0 });
  const emptyCategory = () => ({ total: empty(), backedUp: empty(), pending: empty() });
  const result = {
    total: empty(),
    backedUp: empty(),
    pending: empty(),
    byType: { photo: emptyCategory(), video: emptyCategory(), audio: emptyCategory() },
  };

  try {
    const config = await getCloudConfig();
    const filter = { media_types: config?.media_types };
    const allAssets = await getAllDeviceAssets(filter);
    const backedUp = await getBackedUpIds();

    // Update fast-read cache
    await AsyncStorage.setItem(BACKUP_TOTAL_CACHE_KEY, JSON.stringify({ total: allAssets.length, timestamp: Date.now() })).catch(() => {});

    if (allAssets.length === 0) return result;

    // Report initial total so UI can show progress bar immediately
    if (typeof onProgress === 'function') {
      try { onProgress({ scanned: 0, total: allAssets.length }); } catch {}
    }

    let scanned = 0;
    const BATCH = 50;

    for (let i = 0; i < allAssets.length; i += BATCH) {
      const chunk = allAssets.slice(i, i + BATCH);
      const sizes = await Promise.all(
        chunk.map(async (asset) => {
          try {
            const info = await MediaLibrary.getAssetInfoAsync(asset.id);
            return await resolveFileSize(asset, info);
          } catch {
            return 0;
          }
        })
      );

      for (let j = 0; j < chunk.length; j++) {
        const asset = chunk[j];
        const size = sizes[j];
        const isBackedUp = backedUp.has(asset.id);
        const type = ['photo', 'video', 'audio'].includes(asset.mediaType) ? asset.mediaType : 'photo';

        // Totals
        result.total.count++;
        result.total.size += size;
        result.byType[type].total.count++;
        result.byType[type].total.size += size;

        if (isBackedUp) {
          result.backedUp.count++;
          result.backedUp.size += size;
          result.byType[type].backedUp.count++;
          result.byType[type].backedUp.size += size;
        } else {
          result.pending.count++;
          result.pending.size += size;
          result.byType[type].pending.count++;
          result.byType[type].pending.size += size;
        }
      }

      scanned += chunk.length;
      if (typeof onProgress === 'function') {
        try { onProgress({ scanned, total: allAssets.length }); } catch {}
      }
    }

    return result;
  } catch (err) {
    console.error('[CloudBackup] getDetailedBackupStatus error:', err?.message);
    return result;
  }
}

/**
 * Clear backup tracking (force re-upload on next backup).
 */
export async function clearBackupCache() {
  await AsyncStorage.multiRemove([BACKED_UP_KEY, BACKUP_QUEUE_KEY]);
  console.log('[CloudBackup] Cache cleared — next backup will re-upload all files');
}
