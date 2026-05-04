import ReceiveSharingIntent from 'react-native-receive-sharing-intent';

/**
 * Get shared files from Android share intent.
 * Returns array of { uri, fileName, mimeType, size } or empty array.
 */
export function getSharedFiles() {
  return new Promise((resolve) => {
    ReceiveSharingIntent.getReceivedFiles(
      (files) => {
        resolve(files || []);
      },
      (error) => {
        console.warn('[ShareReceiver] Error getting shared files:', error);
        resolve([]);
      }
    );
  });
}

/**
 * Clear the shared intent after handling (prevent re-triggering on next app open).
 */
export function clearSharedFiles() {
  ReceiveSharingIntent.clearReceivedFiles();
}

