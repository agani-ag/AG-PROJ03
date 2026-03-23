import { Text, StyleSheet } from 'react-native';
import { useAppName } from '../utils/AppContext';

/**
 * Displays the business name text.
 * Props:
 *   textStyle — override style for the name text
 */
export default function AppBrand({ textStyle }) {
  const { appName } = useAppName();
  return <Text style={[styles.name, textStyle]}>{appName}</Text>;
}

const styles = StyleSheet.create({
  name: {
    fontSize: 52,
    fontWeight: '800',
    color: '#1a1a2e',
    letterSpacing: 2,
  },
});
