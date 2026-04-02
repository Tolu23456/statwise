import React from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, Platform,
} from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { Colors } from '@/constants/colors';
import { useTheme } from '@/context/ThemeContext';

export default function InsightDetailScreen() {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { insight: insightParam } = useLocalSearchParams<{ insight: string }>();

  let insight: any = null;
  try {
    insight = insightParam ? JSON.parse(insightParam) : null;
  } catch {
    insight = null;
  }

  if (!insight) {
    return (
      <View style={[styles.container, { backgroundColor: C.background }]}>
        <TouchableOpacity
          style={[styles.backBtn, { paddingTop: (Platform.OS === 'web' ? 67 : insets.top) + 8 }]}
          onPress={() => router.back()}
        >
          <Ionicons name="arrow-back" size={24} color={C.text} />
        </TouchableOpacity>
        <View style={styles.centered}>
          <Text style={[styles.errorText, { color: C.textSecondary }]}>Insight not found.</Text>
        </View>
      </View>
    );
  }

  const topInset = Platform.OS === 'web' ? 67 : insets.top;
  const content: string = insight.content ?? insight.summary ?? '';
  const lines = content.split('\n');

  return (
    <View style={[styles.container, { backgroundColor: C.background }]}>
      <View style={[styles.header, { paddingTop: topInset + 8, backgroundColor: C.background }]}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={C.text} />
        </TouchableOpacity>
        {insight.category && (
          <View style={[styles.catBadge, { backgroundColor: C.goldLight }]}>
            <Text style={[styles.catText, { color: C.gold }]}>{insight.category}</Text>
          </View>
        )}
      </View>

      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          { paddingBottom: insets.bottom + 40 },
        ]}
        showsVerticalScrollIndicator={false}
      >
        <Text style={[styles.title, { color: C.text }]}>{insight.title}</Text>
        <Text style={[styles.date, { color: C.textMuted }]}>
          {new Date(insight.published_at).toLocaleDateString('en-US', {
            weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
          })}
        </Text>

        <View style={[styles.divider, { backgroundColor: C.border }]} />

        {lines.map((line: string, idx: number) => {
          if (line.startsWith('## ')) {
            return (
              <Text key={idx} style={[styles.h2, { color: C.text }]}>
                {line.replace('## ', '')}
              </Text>
            );
          }
          if (line.startsWith('### ')) {
            return (
              <Text key={idx} style={[styles.h3, { color: C.text }]}>
                {line.replace('### ', '')}
              </Text>
            );
          }
          if (line.startsWith('- ')) {
            return (
              <View key={idx} style={styles.bulletRow}>
                <View style={[styles.bullet, { backgroundColor: C.primary }]} />
                <Text style={[styles.bulletText, { color: C.textSecondary }]}>
                  {line.replace('- ', '').replace(/\*\*(.*?)\*\*/g, '$1')}
                </Text>
              </View>
            );
          }
          if (line.match(/^\d+\./)) {
            return (
              <View key={idx} style={styles.bulletRow}>
                <Text style={[styles.numLabel, { color: C.primary }]}>{line.split('.')[0]}.</Text>
                <Text style={[styles.bulletText, { color: C.textSecondary }]}>
                  {line.replace(/^\d+\.\s*/, '').replace(/\*\*(.*?)\*\*/g, '$1')}
                </Text>
              </View>
            );
          }
          if (line.trim() === '') {
            return <View key={idx} style={styles.spacer} />;
          }
          return (
            <Text key={idx} style={[styles.body, { color: C.textSecondary }]}>
              {line.replace(/\*\*(.*?)\*\*/g, '$1')}
            </Text>
          );
        })}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  header: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    paddingHorizontal: 16, paddingBottom: 12,
  },
  backBtn: { padding: 4 },
  catBadge: { paddingHorizontal: 12, paddingVertical: 4, borderRadius: 20 },
  catText: { fontSize: 11, fontFamily: 'Inter_600SemiBold', textTransform: 'uppercase', letterSpacing: 0.5 },
  scroll: { paddingHorizontal: 20, paddingTop: 4 },
  title: { fontSize: 26, fontFamily: 'Inter_700Bold', lineHeight: 34, marginBottom: 8 },
  date: { fontSize: 13, fontFamily: 'Inter_400Regular', marginBottom: 20 },
  divider: { height: 1, marginBottom: 20 },
  h2: { fontSize: 20, fontFamily: 'Inter_700Bold', marginTop: 24, marginBottom: 12 },
  h3: { fontSize: 17, fontFamily: 'Inter_600SemiBold', marginTop: 16, marginBottom: 8 },
  body: { fontSize: 15, fontFamily: 'Inter_400Regular', lineHeight: 24, marginBottom: 4 },
  bulletRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, marginBottom: 8 },
  bullet: { width: 6, height: 6, borderRadius: 3, marginTop: 9 },
  numLabel: { fontSize: 15, fontFamily: 'Inter_700Bold', minWidth: 20 },
  bulletText: { flex: 1, fontSize: 15, fontFamily: 'Inter_400Regular', lineHeight: 22 },
  spacer: { height: 8 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  errorText: { fontSize: 16, fontFamily: 'Inter_400Regular' },
});
