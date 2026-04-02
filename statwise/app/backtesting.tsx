import React from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  ActivityIndicator, Platform, RefreshControl,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useQuery } from '@tanstack/react-query';
import { Ionicons } from '@expo/vector-icons';
import { Colors } from '@/constants/colors';
import { supabase } from '@/lib/supabase';
import { useTheme } from '@/context/ThemeContext';

type PredictionResult = {
  id: string;
  prediction: string;
  actual_result: string | null;
  status: string;
  confidence: number;
  league: string;
  match_title: string;
  match_date: string;
};

type Stats = {
  total: number;
  correct: number;
  incorrect: number;
  pending: number;
  accuracy: number;
  avgConfidence: number;
  byLeague: Record<string, { total: number; correct: number }>;
  byTier: Record<string, { total: number; correct: number }>;
};

function computeStats(results: PredictionResult[]): Stats {
  const completed = results.filter(r => r.actual_result);
  const correct = completed.filter(r => r.prediction === r.actual_result).length;
  const byLeague: Record<string, { total: number; correct: number }> = {};
  const byTier: Record<string, { total: number; correct: number }> = {};

  for (const r of completed) {
    const lg = r.league || 'Unknown';
    byLeague[lg] = byLeague[lg] ?? { total: 0, correct: 0 };
    byLeague[lg].total++;
    if (r.prediction === r.actual_result) byLeague[lg].correct++;
  }

  return {
    total: results.length,
    correct,
    incorrect: completed.length - correct,
    pending: results.length - completed.length,
    accuracy: completed.length > 0 ? Math.round((correct / completed.length) * 100) : 0,
    avgConfidence: results.length > 0
      ? Math.round(results.reduce((s, r) => s + r.confidence, 0) / results.length)
      : 0,
    byLeague,
    byTier,
  };
}

export default function BacktestingScreen() {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const topInset = Platform.OS === 'web' ? 67 : insets.top;

  const { data: results = [], isLoading, refetch, isFetching } = useQuery<PredictionResult[]>({
    queryKey: ['backtesting'],
    queryFn: async () => {
      const thirtyDaysAgo = new Date(Date.now() - 30 * 86400000).toISOString().split('T')[0];
      const { data, error } = await supabase
        .from('predictions')
        .select('id, prediction, actual_result, status, confidence, league, match_title, match_date')
        .gte('match_date', thirtyDaysAgo)
        .order('match_date', { ascending: false });
      if (error) throw error;
      return data ?? [];
    },
    staleTime: 5 * 60 * 1000,
  });

  const stats = computeStats(results);
  const topLeagues = Object.entries(stats.byLeague)
    .sort((a, b) => b[1].total - a[1].total)
    .slice(0, 5);

  return (
    <View style={[styles.container, { backgroundColor: C.background }]}>
      <View style={[styles.header, { paddingTop: topInset + 8, backgroundColor: C.background }]}>
        <TouchableOpacity onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={C.text} />
        </TouchableOpacity>
        <Text style={[styles.headerTitle, { color: C.text }]}>Prediction Accuracy</Text>
        <View style={{ width: 32 }} />
      </View>

      {isLoading ? (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color={C.primary} />
        </View>
      ) : (
        <ScrollView
          contentContainerStyle={[styles.scroll, { paddingBottom: insets.bottom + 40 }]}
          showsVerticalScrollIndicator={false}
          refreshControl={
            <RefreshControl refreshing={isFetching} onRefresh={refetch} tintColor={C.primary} colors={[C.primary]} />
          }
        >
          <Text style={[styles.subtitle, { color: C.textSecondary }]}>Last 30 days</Text>

          <View style={styles.overviewRow}>
            <View style={[styles.overviewCard, { backgroundColor: C.card, borderColor: C.border }]}>
              <Text style={[styles.bigNum, { color: C.primary }]}>{stats.accuracy}%</Text>
              <Text style={[styles.overviewLabel, { color: C.textSecondary }]}>Accuracy</Text>
            </View>
            <View style={[styles.overviewCard, { backgroundColor: C.card, borderColor: C.border }]}>
              <Text style={[styles.bigNum, { color: C.text }]}>{stats.total}</Text>
              <Text style={[styles.overviewLabel, { color: C.textSecondary }]}>Total</Text>
            </View>
            <View style={[styles.overviewCard, { backgroundColor: C.card, borderColor: C.border }]}>
              <Text style={[styles.bigNum, { color: C.success }]}>{stats.correct}</Text>
              <Text style={[styles.overviewLabel, { color: C.textSecondary }]}>Correct</Text>
            </View>
            <View style={[styles.overviewCard, { backgroundColor: C.card, borderColor: C.border }]}>
              <Text style={[styles.bigNum, { color: C.warning }]}>{stats.pending}</Text>
              <Text style={[styles.overviewLabel, { color: C.textSecondary }]}>Pending</Text>
            </View>
          </View>

          <View style={[styles.confCard, { backgroundColor: C.card, borderColor: C.border }]}>
            <View style={styles.confRow}>
              <Text style={[styles.confLabel, { color: C.textSecondary }]}>Average Confidence</Text>
              <Text style={[styles.confValue, { color: C.primary }]}>{stats.avgConfidence}%</Text>
            </View>
            <View style={[styles.confBar, { backgroundColor: C.border }]}>
              <View style={[styles.confFill, { width: `${stats.avgConfidence}%` as any, backgroundColor: C.primary }]} />
            </View>
          </View>

          {stats.accuracy === 0 && stats.pending === stats.total && (
            <View style={[styles.infoCard, { backgroundColor: C.warningLight, borderColor: C.warning }]}>
              <Ionicons name="information-circle-outline" size={20} color={C.warning} />
              <Text style={[styles.infoText, { color: C.warning }]}>
                Accuracy data will appear after predictions are settled. Results are tracked automatically after each match.
              </Text>
            </View>
          )}

          {topLeagues.length > 0 && (
            <View style={[styles.section, { backgroundColor: C.card, borderColor: C.border }]}>
              <Text style={[styles.sectionTitle, { color: C.text }]}>By League</Text>
              {topLeagues.map(([league, s]) => {
                const pct = s.total > 0 ? Math.round((s.correct / s.total) * 100) : 0;
                return (
                  <View key={league} style={styles.leagueRow}>
                    <Text style={[styles.leagueName, { color: C.textSecondary }]} numberOfLines={1}>
                      {league}
                    </Text>
                    <View style={[styles.miniBar, { backgroundColor: C.border }]}>
                      <View style={[styles.miniBarFill, { width: `${pct}%` as any, backgroundColor: C.primary }]} />
                    </View>
                    <Text style={[styles.leaguePct, { color: C.text }]}>{pct}%</Text>
                  </View>
                );
              })}
            </View>
          )}

          {results.slice(0, 20).map(r => (
            <View key={r.id} style={[styles.resultRow, { backgroundColor: C.card, borderColor: C.border }]}>
              <View style={styles.resultInfo}>
                <Text style={[styles.resultMatch, { color: C.text }]} numberOfLines={1}>{r.match_title}</Text>
                <Text style={[styles.resultDate, { color: C.textMuted }]}>{r.match_date}</Text>
              </View>
              <View style={styles.resultRight}>
                <Text style={[styles.resultPred, { color: C.textSecondary }]}>{r.prediction}</Text>
                {r.actual_result ? (
                  <View style={[
                    styles.resultBadge,
                    { backgroundColor: r.prediction === r.actual_result ? C.successLight : C.dangerLight },
                  ]}>
                    <Ionicons
                      name={r.prediction === r.actual_result ? 'checkmark' : 'close'}
                      size={12}
                      color={r.prediction === r.actual_result ? C.success : C.danger}
                    />
                  </View>
                ) : (
                  <View style={[styles.resultBadge, { backgroundColor: C.warningLight }]}>
                    <Ionicons name="time-outline" size={12} color={C.warning} />
                  </View>
                )}
              </View>
            </View>
          ))}
        </ScrollView>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingBottom: 12,
  },
  headerTitle: { fontSize: 18, fontFamily: 'Inter_700Bold' },
  scroll: { paddingHorizontal: 16, paddingTop: 4 },
  subtitle: { fontSize: 13, fontFamily: 'Inter_400Regular', marginBottom: 16 },
  overviewRow: { flexDirection: 'row', gap: 10, marginBottom: 14 },
  overviewCard: { flex: 1, borderRadius: 14, borderWidth: 1, padding: 12, alignItems: 'center', gap: 4 },
  bigNum: { fontSize: 22, fontFamily: 'Inter_700Bold' },
  overviewLabel: { fontSize: 11, fontFamily: 'Inter_400Regular', textAlign: 'center' },
  confCard: { borderRadius: 14, borderWidth: 1, padding: 14, marginBottom: 14 },
  confRow: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 8 },
  confLabel: { fontSize: 14, fontFamily: 'Inter_400Regular' },
  confValue: { fontSize: 14, fontFamily: 'Inter_700Bold' },
  confBar: { height: 6, borderRadius: 3, overflow: 'hidden' },
  confFill: { height: 6, borderRadius: 3 },
  infoCard: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 10,
    borderRadius: 12, borderWidth: 1, padding: 14, marginBottom: 14,
  },
  infoText: { flex: 1, fontSize: 13, fontFamily: 'Inter_400Regular', lineHeight: 18 },
  section: { borderRadius: 14, borderWidth: 1, padding: 14, marginBottom: 14 },
  sectionTitle: { fontSize: 16, fontFamily: 'Inter_700Bold', marginBottom: 12 },
  leagueRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 10 },
  leagueName: { width: 100, fontSize: 13, fontFamily: 'Inter_400Regular' },
  miniBar: { flex: 1, height: 6, borderRadius: 3, overflow: 'hidden' },
  miniBarFill: { height: 6, borderRadius: 3 },
  leaguePct: { width: 36, fontSize: 13, fontFamily: 'Inter_600SemiBold', textAlign: 'right' },
  resultRow: {
    flexDirection: 'row', alignItems: 'center',
    borderRadius: 12, borderWidth: 1, padding: 12, marginBottom: 8,
  },
  resultInfo: { flex: 1 },
  resultMatch: { fontSize: 14, fontFamily: 'Inter_600SemiBold', marginBottom: 2 },
  resultDate: { fontSize: 12, fontFamily: 'Inter_400Regular' },
  resultRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  resultPred: { fontSize: 13, fontFamily: 'Inter_400Regular' },
  resultBadge: { width: 24, height: 24, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center' },
});
