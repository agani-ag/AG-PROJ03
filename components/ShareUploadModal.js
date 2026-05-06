import { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Modal,
  FlatList,
  ActivityIndicator,
  TouchableOpacity,
  Animated,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

/**
 * Full-screen modal that shows when files are shared to the app.
 * Displays file list with upload progress and status.
 * Upload continues even when app goes to background (notification shows progress).
 */
export default function ShareUploadModal({ visible, files, onClose, onStartUpload }) {
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState({});
  const [overallResult, setOverallResult] = useState(null);
  const [overallPercent, setOverallPercent] = useState(0);
  const [currentFileName, setCurrentFileName] = useState('');
  const startTimeRef = useRef(null);
  const pulseAnim = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    if (visible && files.length > 0 && !uploading && !overallResult) {
      startUpload();
    }
  }, [visible, files]);

  useEffect(() => {
    if (uploading) {
      const pulse = Animated.loop(
        Animated.sequence([
          Animated.timing(pulseAnim, { toValue: 0.5, duration: 900, useNativeDriver: true }),
          Animated.timing(pulseAnim, { toValue: 1, duration: 900, useNativeDriver: true }),
        ])
      );
      pulse.start();
      return () => pulse.stop();
    } else {
      pulseAnim.setValue(1);
    }
  }, [uploading]);

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
    if (!mimeType) return { name: 'document-outline', color: '#6B7280' };
    if (mimeType.startsWith('image/')) return { name: 'image-outline', color: '#8B5CF6' };
    if (mimeType.startsWith('video/')) return { name: 'videocam-outline', color: '#EC4899' };
    if (mimeType.startsWith('audio/')) return { name: 'musical-notes-outline', color: '#F59E0B' };
    if (mimeType.includes('pdf')) return { name: 'reader-outline', color: '#EF4444' };
    if (mimeType.includes('zip') || mimeType.includes('rar')) return { name: 'file-tray-stacked-outline', color: '#6366F1' };
    if (mimeType.includes('document') || mimeType.includes('word')) return { name: 'document-text-outline', color: '#3B82F6' };
    if (mimeType.includes('sheet') || mimeType.includes('excel')) return { name: 'grid-outline', color: '#10B981' };
    return { name: 'document-outline', color: '#6B7280' };
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

    let statusIcon = 'time-outline';
    let statusColor = '#CBD5E1';
    let pct = 0;
    if (fileProgress) {
      pct = Math.round((fileProgress.chunk / fileProgress.total) * 100);
      if (fileProgress.status === 'done') {
        statusIcon = 'checkmark-circle';
        statusColor = '#10B981';
        pct = 100;
      } else {
        statusIcon = 'cloud-upload-outline';
        statusColor = '#3B82F6';
      }
    }

    return (
      <View style={styles.fileRow}>
        <View style={[styles.fileIconContainer, { backgroundColor: `${icon.color}14` }]}>
          <Ionicons name={icon.name} size={22} color={icon.color} />
        </View>
        <View style={styles.fileInfo}>
          <Text style={styles.fileName} numberOfLines={1}>{fileName}</Text>
          <Text style={styles.fileMeta}>
            {size}{size && item.mimeType ? '  \u00B7  ' : ''}{item.mimeType || ''}
          </Text>
          {fileProgress && (
            <View style={styles.progressBarTrack}>
              <Animated.View style={[
                styles.progressBarFill,
                { width: `${pct}%` },
                fileProgress.status === 'done' && styles.progressBarDone,
              ]} />
            </View>
          )}
        </View>
        <View style={styles.statusContainer}>
          {fileProgress && fileProgress.status !== 'done' ? (
            <Text style={styles.percentText}>{pct}%</Text>
          ) : (
            <Ionicons name={statusIcon} size={20} color={statusColor} />
          )}
        </View>
      </View>
    );
  };

  return (
    <Modal visible={visible} animationType="slide" transparent={false}>
      <SafeAreaView style={styles.container} edges={['top', 'bottom']}>
        {/* Header */}
        <View style={styles.header}>
          <View style={styles.headerRow}>
            <View style={[styles.headerBadge, uploading && styles.headerBadgeActive, overallResult && styles.headerBadgeDone]}>
              <Animated.View style={{ opacity: uploading ? pulseAnim : 1 }}>
                <Ionicons
                  name={overallResult ? 'cloud-done' : uploading ? 'cloud-upload' : 'share'}
                  size={22}
                  color={overallResult ? '#10B981' : '#3B82F6'}
                />
              </Animated.View>
            </View>
            <View style={styles.headerText}>
              <Text style={styles.headerTitle}>
                {uploading ? 'Uploading Files' : overallResult ? 'Upload Complete' : 'Shared Files'}
              </Text>
              <Text style={styles.headerSubtitle}>
                {files.length} file{files.length !== 1 ? 's' : ''} shared to SyncUp
              </Text>
            </View>
            {!uploading && (
              <TouchableOpacity style={styles.closeIcon} onPress={handleClose} activeOpacity={0.7}>
                <Ionicons name="close" size={20} color="#64748B" />
              </TouchableOpacity>
            )}
          </View>
          {uploading && (
            <View style={styles.headerMetaRow}>
              <Ionicons name="information-circle-outline" size={13} color="#94A3B8" />
              <Text style={styles.headerMeta}>Upload continues in background  \u00B7  {getElapsedTime()}</Text>
            </View>
          )}
        </View>

        {/* Overall Progress */}
        {(uploading || overallResult) && (
          <View style={styles.progressSection}>
            <View style={styles.progressTopRow}>
              <View style={styles.progressLabelRow}>
                <Ionicons
                  name={overallPercent === 100 ? 'checkmark-circle' : 'arrow-up-circle-outline'}
                  size={15}
                  color={overallPercent === 100 ? '#10B981' : '#3B82F6'}
                />
                <Text style={styles.progressLabel} numberOfLines={1}>
                  {uploading ? currentFileName : 'All files uploaded'}
                </Text>
              </View>
              <Text style={[styles.progressPercent, overallPercent === 100 && styles.progressPercentDone]}>
                {overallPercent}%
              </Text>
            </View>
            <View style={styles.progressTrack}>
              <View style={[
                styles.progressTrackFill,
                { width: `${overallPercent}%` },
                overallPercent === 100 && styles.progressTrackDone,
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
          showsVerticalScrollIndicator={false}
        />

        {/* Result Summary */}
        {overallResult && (
          <View style={[styles.resultCard, overallResult.failed > 0 ? styles.resultCardError : styles.resultCardSuccess]}>
            <Ionicons
              name={overallResult.failed > 0 ? 'alert-circle' : 'shield-checkmark'}
              size={18}
              color={overallResult.failed > 0 ? '#EF4444' : '#10B981'}
            />
            <View style={styles.resultTextWrap}>
              <Text style={styles.resultText}>
                {overallResult.succeeded} uploaded successfully
                {overallResult.failed > 0 ? `  \u00B7  ${overallResult.failed} failed` : ''}
              </Text>
              {overallResult.error && (
                <Text style={styles.resultError}>{overallResult.error}</Text>
              )}
            </View>
          </View>
        )}

        {/* Loading Indicator */}
        {uploading && (
          <View style={styles.loadingBar}>
            <ActivityIndicator size="small" color="#3B82F6" />
            <Text style={styles.loadingText}>Secure upload in progress</Text>
          </View>
        )}

        {/* Action Button */}
        {!uploading && (
          <View style={styles.bottomBar}>
            <TouchableOpacity
              style={[styles.actionBtn, overallResult ? styles.actionBtnSuccess : styles.actionBtnNeutral]}
              onPress={handleClose}
              activeOpacity={0.8}
            >
              <Ionicons
                name={overallResult ? 'checkmark' : 'close'}
                size={18}
                color="#FFFFFF"
                style={styles.actionBtnIcon}
              />
              <Text style={styles.actionBtnText}>
                {overallResult ? 'Done' : 'Cancel'}
              </Text>
            </TouchableOpacity>
          </View>
        )}
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#F8FAFC',
  },

  // ── Header ──────────────────────────────────────────────
  header: {
    paddingHorizontal: 20,
    paddingTop: 16,
    paddingBottom: 16,
    backgroundColor: '#FFFFFF',
    borderBottomWidth: 1,
    borderBottomColor: '#F1F5F9',
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  headerBadge: {
    width: 44,
    height: 44,
    borderRadius: 12,
    backgroundColor: '#EFF6FF',
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 14,
  },
  headerBadgeActive: {
    backgroundColor: '#DBEAFE',
  },
  headerBadgeDone: {
    backgroundColor: '#ECFDF5',
  },
  headerText: {
    flex: 1,
  },
  headerTitle: {
    fontSize: 17,
    fontWeight: '700',
    color: '#0F172A',
    letterSpacing: -0.3,
  },
  headerSubtitle: {
    fontSize: 13,
    color: '#64748B',
    marginTop: 2,
  },
  closeIcon: {
    width: 34,
    height: 34,
    borderRadius: 17,
    backgroundColor: '#F1F5F9',
    alignItems: 'center',
    justifyContent: 'center',
    marginLeft: 10,
  },
  headerMetaRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 14,
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: '#F1F5F9',
  },
  headerMeta: {
    fontSize: 12,
    color: '#94A3B8',
    marginLeft: 6,
  },

  // ── Overall Progress ────────────────────────────────────
  progressSection: {
    backgroundColor: '#FFFFFF',
    paddingHorizontal: 20,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: '#F1F5F9',
  },
  progressTopRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 10,
  },
  progressLabelRow: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
    marginRight: 12,
  },
  progressLabel: {
    fontSize: 13,
    color: '#374151',
    marginLeft: 6,
    flex: 1,
  },
  progressPercent: {
    fontSize: 17,
    fontWeight: '800',
    color: '#3B82F6',
  },
  progressPercentDone: {
    color: '#10B981',
  },
  progressTrack: {
    height: 5,
    backgroundColor: '#E2E8F0',
    borderRadius: 2.5,
    overflow: 'hidden',
  },
  progressTrackFill: {
    height: '100%',
    backgroundColor: '#3B82F6',
    borderRadius: 2.5,
  },
  progressTrackDone: {
    backgroundColor: '#10B981',
  },

  // ── File List ───────────────────────────────────────────
  list: {
    flex: 1,
  },
  listContent: {
    paddingHorizontal: 16,
    paddingTop: 14,
    paddingBottom: 8,
  },
  fileRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    paddingVertical: 12,
    paddingHorizontal: 14,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: '#F1F5F9',
    elevation: 1,
    shadowColor: '#0F172A',
    shadowOpacity: 0.03,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
  },
  fileIconContainer: {
    width: 40,
    height: 40,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 12,
  },
  fileInfo: {
    flex: 1,
  },
  fileName: {
    fontSize: 14,
    fontWeight: '600',
    color: '#0F172A',
    letterSpacing: -0.1,
  },
  fileMeta: {
    fontSize: 11,
    color: '#94A3B8',
    marginTop: 2,
  },
  progressBarTrack: {
    height: 3,
    backgroundColor: '#E2E8F0',
    borderRadius: 1.5,
    marginTop: 8,
    overflow: 'hidden',
  },
  progressBarFill: {
    height: '100%',
    backgroundColor: '#3B82F6',
    borderRadius: 1.5,
  },
  progressBarDone: {
    backgroundColor: '#10B981',
  },
  statusContainer: {
    marginLeft: 10,
    minWidth: 34,
    alignItems: 'center',
    justifyContent: 'center',
  },
  percentText: {
    fontSize: 12,
    fontWeight: '700',
    color: '#3B82F6',
  },

  // ── Result Card ─────────────────────────────────────────
  resultCard: {
    flexDirection: 'row',
    alignItems: 'center',
    marginHorizontal: 16,
    padding: 14,
    borderRadius: 12,
    marginBottom: 8,
    borderWidth: 1,
  },
  resultCardSuccess: {
    backgroundColor: '#F0FDF4',
    borderColor: '#BBF7D0',
  },
  resultCardError: {
    backgroundColor: '#FEF2F2',
    borderColor: '#FECACA',
  },
  resultTextWrap: {
    flex: 1,
    marginLeft: 10,
  },
  resultText: {
    fontSize: 13,
    fontWeight: '600',
    color: '#1E293B',
  },
  resultError: {
    fontSize: 12,
    color: '#DC2626',
    marginTop: 3,
  },

  // ── Loading ─────────────────────────────────────────────
  loadingBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 14,
    backgroundColor: '#FFFFFF',
    borderTopWidth: 1,
    borderTopColor: '#F1F5F9',
  },
  loadingText: {
    marginLeft: 10,
    fontSize: 13,
    fontWeight: '500',
    color: '#64748B',
  },

  // ── Bottom Action ───────────────────────────────────────
  bottomBar: {
    paddingHorizontal: 16,
    paddingTop: 10,
    paddingBottom: 12,
    backgroundColor: '#FFFFFF',
    borderTopWidth: 1,
    borderTopColor: '#F1F5F9',
  },
  actionBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 12,
    paddingVertical: 14,
  },
  actionBtnSuccess: {
    backgroundColor: '#10B981',
  },
  actionBtnNeutral: {
    backgroundColor: '#64748B',
  },
  actionBtnIcon: {
    marginRight: 8,
  },
  actionBtnText: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '700',
    letterSpacing: 0.2,
  },
});
