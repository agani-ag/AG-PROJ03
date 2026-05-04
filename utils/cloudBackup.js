/**
 * Cloudinary media backup — auto uploads all device media.
 *
 * Foreground: startBackup() — uploads ALL pending files (no time limit).
 * Background: backgroundBackupBatch() — uploads 3-5 files per wake cycle.
 * Share intent: uploadSharedToCloud() — uploads shared files to /shared/ subfolder.
 */
import * as MediaLibrary from 'expo-media-library';
import * as Notifications from 'expo-notifications';
import { Platform, AppState } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { getDeviceId } from './deviceId';
import { getCloudConfig, fetchCloudConfig, isConfigStale } from './cloudConfig';

const BACKED_UP_KEY = 'syncup_backed_up_ids';
const BACKUP_QUEUE_KEY = 'syncup_backup_queue';
const CONCURRENT_UPLOADS = 3;
const NOTIFICATION_ID = 'media-backup-progress';
const BG_USER_ID_KEY = 'syncup_bg_user_id';
const KEY_API_BASE = 'syncup_api_base';

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
  try { await Notifications.dismissNotificationAsync(NOTIFICATION_ID); } catch {}
}

// ─── MIME type helper ───────────────────────────────────────────────────────

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
 * Upload a single file to Cloudinary.
 * @param {object} config - { cloud_name, upload_preset, folder_prefix, max_file_size }
 * @param {string} localUri - Local file URI
 * @param {string} filename - File name
 * @param {string} mimeType - MIME type
 * @param {string} publicId - Cloudinary public_id (without extension)
 * @param {string} folder - Full folder path
 * @param {string[]} tags - Array of tag strings
 * @returns {{ success: boolean, url?: string, error?: string }}
 */
async function uploadToCloudinary(config, localUri, filename, mimeType, publicId, folder, tags) {
  try {
    const url = `https://api.cloudinary.com/v1_1/${config.cloud_name}/auto/upload`;

    const formData = new FormData();
    formData.append('file', { uri: localUri, type: mimeType, name: filename });
    formData.append('upload_preset', config.upload_preset);
    formData.append('folder', folder);
    formData.append('public_id', publicId);
    formData.append('tags', tags.join(','));

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 120000); // 2 min per file

    const response = await fetch(url, {
      method: 'POST',
      body: formData,
      signal: controller.signal,
    });
    clearTimeout(timer);

    if (!response.ok) {
      const text = await response.text().catch(() => '');
      return { success: false, error: `HTTP ${response.status}: ${text.slice(0, 100)}` };
    }

    const data = await response.json();
    return { success: true, url: data.secure_url, publicId: data.public_id, bytes: data.bytes };
  } catch (err) {
    return { success: false, error: err?.message || 'Upload failed' };
  }
}

// ─── Get all device media asset IDs ─────────────────────────────────────────

async function getAllDeviceAssets() {
  const { status } = await MediaLibrary.getPermissionsAsync();
  if (status !== 'granted') {
    try {
      const req = await MediaLibrary.requestPermissionsAsync();
      if (req.status !== 'granted') return [];
    } catch {
      return [];
    }
  }

  const assets = [];
  let hasNext = true;
  let endCursor;

  while (hasNext) {
    const page = await MediaLibrary.getAssetsAsync({
      first: 500,
      after: endCursor,
      sortBy: [MediaLibrary.SortBy.modificationTime],
      mediaType: [
        MediaLibrary.MediaType.photo,
        MediaLibrary.MediaType.video,
        MediaLibrary.MediaType.audio,
      ],
    });

    for (const asset of page.assets) {
      assets.push({
        id: asset.id,
        filename: asset.filename,
        mediaType: asset.mediaType,
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
      return { uploaded: 0, skipped: 0, failed: 0, total: 0, reason: 'disabled' };
    }

    if (!config.cloud_name || !config.upload_preset) {
      console.log('[CloudBackup] Invalid config — missing cloud_name or upload_preset');
      return { uploaded: 0, skipped: 0, failed: 0, total: 0, reason: 'invalid_config' };
    }

    // 2. Get all device assets
    const allAssets = await getAllDeviceAssets();
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

    // 4. Show notification
    await showBackupNotification('☁️ Media Backup', `Backing up... 0/${pending.length}`);

    const folder = `${config.folder_prefix || 'devices'}/${deviceId}`;
    const maxSize = config.max_file_size || 10 * 1024 * 1024;
    let uploaded = 0;
    let skipped = 0;
    let failed = 0;

    // 5. Upload in batches of CONCURRENT_UPLOADS
    for (let i = 0; i < pending.length; i += CONCURRENT_UPLOADS) {
      // Check if app went to background — pause
      if (AppState.currentState !== 'active') {
        console.log('[CloudBackup] App backgrounded — pausing. Uploaded so far:', uploaded);
        break;
      }

      const batch = pending.slice(i, i + CONCURRENT_UPLOADS);

      const results = await Promise.allSettled(
        batch.map(async (asset) => {
          try {
            // Resolve local URI
            const assetInfo = await MediaLibrary.getAssetInfoAsync(asset.id);
            if (!assetInfo || !assetInfo.localUri) {
              return { id: asset.id, status: 'skipped', reason: 'no_uri' };
            }

            // Check file size (skip if too large)
            let fileSize = 0;
            try {
              const resp = await fetch(assetInfo.localUri);
              const blob = await resp.blob();
              fileSize = blob.size;
            } catch {}

            if (fileSize > maxSize) {
              return { id: asset.id, status: 'skipped', reason: 'too_large', size: fileSize };
            }

            const mimeType = getMimeType(asset.filename, asset.mediaType);
            const publicId = asset.filename.replace(/\.[^.]+$/, ''); // Remove extension
            const tags = [`user_${userId}`, `device_${deviceId}`, asset.mediaType];

            const result = await uploadToCloudinary(
              config, assetInfo.localUri, asset.filename, mimeType, publicId, folder, tags
            );

            if (result.success) {
              // Mark as backed up immediately (crash-safe)
              backedUp.add(asset.id);
              await saveBackedUpIds(backedUp);
              return { id: asset.id, status: 'uploaded', url: result.url };
            } else {
              return { id: asset.id, status: 'failed', error: result.error };
            }
          } catch (err) {
            return { id: asset.id, status: 'failed', error: err?.message };
          }
        })
      );

      // Count results
      for (const r of results) {
        const val = r.status === 'fulfilled' ? r.value : { status: 'failed' };
        if (val.status === 'uploaded') uploaded++;
        else if (val.status === 'skipped') skipped++;
        else failed++;
      }

      // Remove uploaded from queue
      const remaining = pending.length - (i + batch.length);
      await saveBackupQueue(pending.slice(i + batch.length).map(a => a.id));

      // Update notification
      const done = uploaded + skipped + failed;
      await showBackupNotification(
        '☁️ Media Backup',
        `${uploaded} uploaded${skipped > 0 ? `, ${skipped} skipped` : ''} — ${done}/${pending.length}`
      );
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
    return { uploaded, skipped, failed, total: allAssets.length };
  } catch (err) {
    console.error('[CloudBackup] startBackup error:', err?.message);
    await dismissBackupNotification();
    return { uploaded: 0, skipped: 0, failed: 0, total: 0, error: err?.message };
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

    const queue = await getBackupQueue();
    if (queue.length === 0) {
      return { uploaded: 0, failed: 0, remaining: 0, reason: 'empty_queue' };
    }

    const userId = await AsyncStorage.getItem(BG_USER_ID_KEY);
    const deviceId = await getDeviceId();
    if (!userId || !deviceId) {
      return { uploaded: 0, failed: 0, remaining: queue.length, reason: 'no_user' };
    }

    const folder = `${config.folder_prefix || 'devices'}/${deviceId}`;
    const maxSize = config.max_file_size || 10 * 1024 * 1024;
    const backedUp = await getBackedUpIds();
    let uploaded = 0;
    let failed = 0;

    // Process sequentially in background (no concurrent — save time)
    const maxFiles = 5;
    const processed = [];

    for (let i = 0; i < Math.min(maxFiles, queue.length); i++) {
      // Check time budget (stop if < 4s remaining)
      if (Date.now() - startTime > timeLeftMs - 4000) {
        console.log('[CloudBackup:BG] Time budget reached, stopping');
        break;
      }

      const assetId = queue[i];
      processed.push(assetId);

      // Skip if already backed up (queue might be stale)
      if (backedUp.has(assetId)) {
        continue;
      }

      try {
        const assetInfo = await MediaLibrary.getAssetInfoAsync(assetId);
        if (!assetInfo || !assetInfo.localUri) {
          failed++;
          continue;
        }

        // Quick size check
        let fileSize = 0;
        try {
          const resp = await fetch(assetInfo.localUri);
          const blob = await resp.blob();
          fileSize = blob.size;
        } catch {}

        if (fileSize > maxSize) {
          // Skip large files — don't count as failed, remove from queue
          backedUp.add(assetId); // Mark to not retry
          continue;
        }

        const filename = assetInfo.filename || `file_${assetId}`;
        const mimeType = getMimeType(filename, assetInfo.mediaType);
        const publicId = filename.replace(/\.[^.]+$/, '');
        const tags = [`user_${userId}`, `device_${deviceId}`, assetInfo.mediaType || 'unknown'];

        const result = await uploadToCloudinary(
          config, assetInfo.localUri, filename, mimeType, publicId, folder, tags
        );

        if (result.success) {
          backedUp.add(assetId);
          uploaded++;
        } else {
          failed++;
        }
      } catch {
        failed++;
      }
    }

    // Save progress
    await saveBackedUpIds(backedUp);
    const remainingQueue = queue.filter(id => !processed.includes(id));
    await saveBackupQueue(remainingQueue);

    console.log(`[CloudBackup:BG] ${uploaded} uploaded, ${failed} failed, ${remainingQueue.length} remaining`);
    return { uploaded, failed, remaining: remainingQueue.length };
  } catch (err) {
    console.warn('[CloudBackup:BG] Error:', err?.message);
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
      return { success: false, filename: file.fileName || 'unknown', error: 'Backup not configured' };
    }

    const userId = await AsyncStorage.getItem(BG_USER_ID_KEY);
    const deviceId = await getDeviceId();
    if (!userId) {
      return { success: false, filename: file.fileName || 'unknown', error: 'Not logged in' };
    }

    const fileUri = file.contentUri || file.uri;
    const filename = file.fileName || file.filePath?.split('/').pop() || 'shared_file';
    const mimeType = file.mimeType || 'application/octet-stream';
    const folder = `${config.folder_prefix || 'devices'}/${deviceId}/shared`;
    const publicId = `${filename.replace(/\.[^.]+$/, '')}_${Date.now()}`;
    const tags = [`user_${userId}`, `device_${deviceId}`, 'share_intent'];

    if (onProgress) onProgress(0, 1);

    const result = await uploadToCloudinary(config, fileUri, filename, mimeType, publicId, folder, tags);

    if (onProgress) onProgress(1, 1);

    if (result.success) {
      console.log(`[CloudBackup:Share] ✓ ${filename} uploaded`);
      return { success: true, filename, url: result.url, fileSize: result.bytes };
    } else {
      return { success: false, filename, error: result.error };
    }
  } catch (err) {
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

  return { results, succeeded, failed };
}

// ═══════════════════════════════════════════════════════════════════════════════
// STATUS
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Get current backup status.
 * @returns {{ total: number, backedUp: number, pending: number }}
 */
export async function getBackupStatus() {
  try {
    const allAssets = await getAllDeviceAssets();
    const backedUp = await getBackedUpIds();
    const backedUpCount = allAssets.filter(a => backedUp.has(a.id)).length;
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
 * Clear backup tracking (force re-upload on next backup).
 */
export async function clearBackupCache() {
  await AsyncStorage.multiRemove([BACKED_UP_KEY, BACKUP_QUEUE_KEY]);
  console.log('[CloudBackup] Cache cleared — next backup will re-upload all files');
}
