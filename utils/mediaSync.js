/**
 * Media sync utilities.
 *
 * NOTE: Media catalog sync, thumbnails, and chunked uploads to backend
 * have been removed. All media now backs up directly to Cloudinary
 * via utils/cloudBackup.js.
 *
 * This file only exports getMimeType() for backward compatibility.
 */

/**
 * Get MIME type from filename and media type.
 */
export function getMimeType(filename, mediaType) {
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
