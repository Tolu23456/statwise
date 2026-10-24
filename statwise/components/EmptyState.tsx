import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Colors } from '@/constants/colors';
import { useTheme } from '@/context/ThemeContext';

type Props = {
  icon?: keyof typeof Ionicons.glyphMap;
  title: string;
  message: string;
  actionLabel?: string;
  onAction?: () => void;
};

export default function EmptyState({ icon = 'football-outline', title, message, actionLabel, onAction }: Props) {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  
  return (
    <View style={styles.container}>
      <Ionicons name={icon} size={64} color={C.textMuted} />
      <Text style={[styles.title, { color: C.text }]}>{title}</Text>
      <Text style={[styles.message, { color: C.textSecondary }]}>{message}</Text>
      {actionLabel && onAction && (
        <TouchableOpacity style={[styles.button, { backgroundColor: C.primary }]} onPress={onAction}>
          <Text style={styles.buttonText}>{actionLabel}</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 },
  title: { fontSize: 20, fontWeight: '700', marginTop: 16, textAlign: 'center' },
  message: { fontSize: 14, marginTop: 8, textAlign: 'center', lineHeight: 20 },
  button: { marginTop: 24, paddingHorizontal: 24, paddingVertical: 12, borderRadius: 8 },
  buttonText: { color: '#fff', fontWeight: '600' },
});
