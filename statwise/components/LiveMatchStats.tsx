import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Colors } from '@/constants/colors';
import { useTheme } from '@/context/ThemeContext';

type Props = {
  stats?: {
    possession?: { home: number; away: number };
    shots?: { home: number; away: number };
    shotsOnTarget?: { home: number; away: number };
    corners?: { home: number; away: number };
    fouls?: { home: number; away: number };
    yellowCards?: { home: number; away: number };
    redCards?: { home: number; away: number };
  };
};

export default function LiveMatchStats({ stats }: Props) {
  const { theme } = useTheme();
  const C = Colors[theme];
  
  if (!stats) return null;
  
  const rows = [
    { label: 'Possession', home: stats.possession?.home, away: stats.possession?.away, suffix: '%' },
    { label: 'Shots', home: stats.shots?.home, away: stats.shots?.away },
    { label: 'Shots on Target', home: stats.shotsOnTarget?.home, away: stats.shotsOnTarget?.away },
    { label: 'Corners', home: stats.corners?.home, away: stats.corners?.away },
    { label: 'Fouls', home: stats.fouls?.home, away: stats.fouls?.away },
    { label: 'Yellow Cards', home: stats.yellowCards?.home, away: stats.yellowCards?.away },
  ];
  
  return (
    <View style={[styles.container, { backgroundColor: C.card, borderColor: C.border }]}>
      <Text style={[styles.title, { color: C.text }]}>Live Statistics</Text>
      {rows.map((row, i) => (
        <View key={i} style={styles.row}>
          <Text style={[styles.value, { color: C.text }]}>{row.home ?? '-'}</Text>
          <Text style={[styles.label, { color: C.textSecondary }]}>{row.label}{row.suffix || ''}</Text>
          <Text style={[styles.value, { color: C.text }]}>{row.away ?? '-'}</Text>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { borderRadius: 12, borderWidth: 1, padding: 16, marginTop: 16 },
  title: { fontSize: 16, fontWeight: '700', marginBottom: 12 },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 8 },
  value: { fontSize: 14, fontWeight: '600', flex: 1, textAlign: 'center' },
  label: { fontSize: 12, flex: 2, textAlign: 'center' },
});
