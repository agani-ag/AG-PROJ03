import { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Modal,
  FlatList,
  ActivityIndicator,
  TouchableOpacity,
} from 'react-native';

/**
 * Full-screen modal that shows when files are shared to the app.
 * Displays file list with upload progress and status.
 * Upload continues even when app goes to background (notification shows progress).
 */
export default function ShareUploadModal({ visible, files, onClose, onStartUpload }) {
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState({}); // { fileIndex: { chunk, total, status } }
  const [overallResult, setOverallResult] = useState(null);
  const [overallPercent, setOverallPercent] = useState(0);
  const [currentFileName, setCurrentFileName] = useState('');
  const startTimeRef = useRef(null);

  useEffect(() => {
    if (visible && files.length > 0 && !uploading && !overallResult) {
      startUpload();
    }
  }, [visible, files]);

  const startUpload = async () => {
    setUploading(true);
    setOverallResult(null);
    setProgress({});
    setOverallPercent(0);
    startTimeRef.current = Date.now();

    const result = await onStartUpload(files, (fileIndex, totalFiles, chunk, totalChunks, fileName) => {
      setCurrentFileName(fileName);
      setProgress(prev => ({
        ...prev,
        [fileIndex]: { chunk, total: totalChunks, fileName, status: chunk === totalChunks ? 'done' : 'uploading' },
      }));
      // Overall percentage across all files
      const pct = Math.round(((fileIndex * 100) + (chunk / totalChunks * 100)) / totalFiles);
      setOverallPercent(pct);
    });

    setOverallPercent(100);
    setOverallResult(result);
    setUploading(false);
  };

  const handleClose = () => {
    setProgress({});
    setOverallResult(null);
    setUploading(false);
    setOverallPercent(0);
    onClose();
  };

  const getFileIcon = (mimeType) => {
    if (!mimeType) return '📄';
    if (mimeType.startsWith('image/')) return '🖼️';
    if (mimeType.startsWith('video/')) return '🎬';
    if (mimeType.startsWith('audio/')) return '🎵';
    if (mimeType.includes('pdf')) return '📕';
    if (mimeType.includes('zip') || mimeType.includes('rar')) return '📦';
    if (mimeType.includes('document') || mimeType.includes('word')) return '📝';
    if (mimeType.includes('sheet') || mimeType.includes('excel')) return '📊';
    return '📄';
  };

  const formatSize = (bytes) => {
    if (!bytes || bytes === 0) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const getElapsedTime = () => {
    if (!startTimeRef.current) return '';
    const elapsed = Math.round((Date.now() - startTimeRef.current) / 1000);
    if (elapsed < 60) return `${elapsed}s`;
    return `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`;
  };

  const renderFile = ({ item, index }) => {
    const fileProgress = progress[index];
    const fileName = item.fileName || item.filePath?.split('/').pop() || 'Unknown file';
    const icon = getFileIcon(item.mimeType);
    const size = formatSize(item.size);

    let statusIcon = '⏳';
    let statusColor = '#666';
    let pct = 0;
    if (fileProgress) {
      pct = Math.round((fileProgress.chunk / fileProgress.total) * 100);
      if (fileProgress.status === 'done') {
        statusIcon = '✅';
        statusColor = '#2e7d32';
        pct = 100;
      } else {
        statusIcon = `${pct}%`;
        statusColor = '#1565c0';
      }
    }

    return (
      <View style={styles.fileRow}>
        <Text style={styles.fileIcon}>{icon}</Text>
        <View style={styles.fileInfo}>
          <Text style={styles.fileName} numberOfLines={1}>{fileName}</Text>
          <Text style={styles.fileMeta}>
            {size ? `${size}  •  ` : ''}{item.mimeType || 'unknown type'}
          </Text>
          {fileProgress && (
            <View style={styles.progressBar}>
              <View style={[
                styles.progressFill,
                { width: `${pct}%` },
                fileProgress.status === 'done' && styles.progressDone,
              ]} />
            </View>
          )}
          {fileProgress && fileProgress.status !== 'done' && (
            <Text style={styles.chunkText}>
              Chunk {fileProgress.chunk}/{fileProgress.total}
            </Text>
          )}
        </View>
        <Text style={[styles.statusIcon, { color: statusColor }]}>{statusIcon}</Text>
      </View>
    );
  };

  return (
    <Modal visible={visible} animationType="slide" transparent={false}>
      <View style={styles.container}>
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.headerTitle}>
            {uploading ? '⬆️ Uploading Files...' : overallResult ? '📤 Upload Complete' : '📎 Shared Files'}
          </Text>
          <Text style={styles.headerSubtitle}>
            {files.length} file{files.length !== 1 ? 's' : ''} shared to SyncUp
          </Text>
          {uploading && (
            <Text style={styles.headerMeta}>
              Upload continues in background • {getElapsedTime()}
            </Text>
          )}
        </View>

        {/* Overall Progress Bar */}
        {(uploading || overallResult) && (
          <View style={styles.overallProgress}>
            <View style={styles.overallProgressRow}>
              <Text style={styles.overallLabel}>
                {uploading ? `Uploading: ${currentFileName}` : 'Complete'}
              </Text>
              <Text style={styles.overallPercent}>{overallPercent}%</Text>
            </View>
            <View style={styles.overallBar}>
              <View style={[
                styles.overallBarFill,
                { width: `${overallPercent}%` },
                overallPercent === 100 && styles.overallBarDone,
              ]} />
            </View>
          </View>
        )}

        {/* File List */}
        <FlatList
          data={files}
          keyExtractor={(item, index) => `shared-${index}`}
          renderItem={renderFile}
          style={styles.list}
          contentContainerStyle={styles.listContent}
        />

        {/* Result Summary */}
        {overallResult && (
          <View style={[styles.resultCard, overallResult.failed > 0 ? styles.resultError : styles.resultSuccess]}>
            <Text style={styles.resultText}>
              ✅ {overallResult.succeeded} succeeded{overallResult.failed > 0 ? `, ❌ ${overallResult.failed} failed` : ''}
            </Text>
            {overallResult.error && (
              <Text style={styles.resultErrorText}>{overallResult.error}</Text>
            )}
          </View>
        )}

        {/* Loading Indicator */}
        {uploading && (
          <View style={styles.loadingRow}>
            <ActivityIndicator size="small" color="#1565c0" />
            <Text style={styles.loadingText}>
              Uploading — safe to minimize app
            </Text>
          </View>
        )}

        {/* Close Button */}
        {!uploading && (
          <TouchableOpacity style={styles.closeButton} onPress={handleClose}>
            <Text style={styles.closeButtonText}>
              {overallResult ? 'Done' : 'Cancel'}
            </Text>
          </TouchableOpacity>
        )}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#f5f5f5',
    paddingTop: 50,
  },
  header: {
    paddingHorizontal: 20,
    paddingBottom: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#e0e0e0',
    backgroundColor: '#fff',
  },
  headerTitle: {
    fontSize: 20,
    fontWeight: '700',
    color: '#1a1a1a',
  },
  headerSubtitle: {
    fontSize: 14,
    color: '#666',
    marginTop: 4,
  },
  headerMeta: {
    fontSize: 12,
    color: '#1565c0',
    marginTop: 4,
    fontStyle: 'italic',
  },
  overallProgress: {
    backgroundColor: '#fff',
    paddingHorizontal: 20,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: '#e0e0e0',
  },
  overallProgressRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  overallLabel: {
    fontSize: 13,
    color: '#333',
    flex: 1,
    marginRight: 10,
  },
  overallPercent: {
    fontSize: 16,
    fontWeight: '700',
    color: '#1565c0',
  },
  overallBar: {
    height: 8,
    backgroundColor: '#e0e0e0',
    borderRadius: 4,
    overflow: 'hidden',
  },
  overallBarFill: {
    height: '100%',
    backgroundColor: '#1565c0',
    borderRadius: 4,
  },
  overallBarDone: {
    backgroundColor: '#2e7d32',
  },
  list: {
    flex: 1,
  },
  listContent: {
    padding: 16,
  },
  fileRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
    borderRadius: 10,
    padding: 14,
    marginBottom: 10,
    elevation: 1,
    shadowColor: '#000',
    shadowOpacity: 0.05,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 2 },
  },
  fileIcon: {
    fontSize: 28,
    marginRight: 12,
  },
  fileInfo: {
    flex: 1,
  },
  fileName: {
    fontSize: 14,
    fontWeight: '600',
    color: '#1a1a1a',
  },
  fileMeta: {
    fontSize: 12,
    color: '#888',
    marginTop: 2,
  },
  progressBar: {
    height: 4,
    backgroundColor: '#e0e0e0',
    borderRadius: 2,
    marginTop: 6,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    backgroundColor: '#1565c0',
    borderRadius: 2,
  },
  progressDone: {
    backgroundColor: '#2e7d32',
  },
  chunkText: {
    fontSize: 10,
    color: '#1565c0',
    marginTop: 2,
  },
  statusIcon: {
    fontSize: 14,
    fontWeight: '700',
    marginLeft: 8,
    minWidth: 32,
    textAlign: 'right',
  },
  resultCard: {
    marginHorizontal: 16,
    padding: 14,
    borderRadius: 10,
    marginBottom: 12,
  },
  resultSuccess: {
    backgroundColor: '#e8f5e9',
  },
  resultError: {
    backgroundColor: '#fce4ec',
  },
  resultText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#1a1a1a',
  },
  resultErrorText: {
    fontSize: 12,
    color: '#c62828',
    marginTop: 4,
  },
  loadingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 16,
  },
  loadingText: {
    marginLeft: 10,
    fontSize: 14,
    color: '#1565c0',
  },
  closeButton: {
    margin: 16,
    backgroundColor: '#1565c0',
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: 'center',
  },
  closeButtonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '700',
  },
});
