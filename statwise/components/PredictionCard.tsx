import React from 'react';
import { View, Text, StyleSheet, useColorScheme, Platform } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Colors } from '@/constants/colors';
import { Prediction } from '@/lib/supabase';

type Props = {
  prediction: Prediction;
  locked?: boolean;
  nextTier?: string;
};

function formatKickoff(raw: string): string {
  try {
    const d = new Date(raw);
    if (isNaN(d.getTime())) return raw;
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' });
  } catch {
    return raw;
  }
}

function getConfidenceColor(confidence: number, C: typeof Colors.dark) {
  if (confidence >= 75) return C.success;
  if (confidence >= 50) return C.warning;
  return C.danger;
}

function RedactedBlock({ width, height = 14, C }: { width: number | string; height?: number; C: typeof Colors.dark }) {
  return (
    <View
      style={[
        styles.redactedBlock,
        { width: width as any, height, backgroundColor: C.border, opacity: 0.6 },
      ]}
    />
  );
}

export function PredictionCard({ prediction, locked = false, nextTier = 'Premium Tier' }: Props) {
  const scheme = useColorScheme() ?? 'dark';
  const C = Colors[scheme];
  const confColor = getConfidenceColor(prediction.confidence, C);

  const blurStyle = Platform.OS === 'web'
    ? ({ filter: 'blur(6px)', userSelect: 'none' } as any)
    : { opacity: 0.08 };

  if (locked) {
    return (
      <View style={[styles.card, styles.lockedCard, { backgroundColor: C.card, borderColor: C.border }]}>
        <View style={styles.header}>
          <Text style={[styles.league, { color: C.textMuted, backgroundColor: C.inputBg }]}>
            {prediction.league}
          </Text>
          <Text style={[styles.time, { color: C.textMuted }]}>
            {formatKickoff(prediction.kickoff_time)}
          </Text>
        </View>

        <Text style={[styles.matchTitle, { color: C.textSecondary }]} numberOfLines={1}>
          {prediction.match_title}
        </Text>

        <View style={[styles.blurredSection, blurStyle]}>
          <View style={styles.row}>
            <View style={[styles.predBadge, { backgroundColor: C.primaryLight }]}>
              <Text style={[styles.predText, { color: C.primary }]}>{prediction.prediction}</Text>
            </View>
            <Text style={[styles.odds, { color: C.textSecondary }]}>Odds: {prediction.odds}</Text>
          </View>
          <View style={styles.confidenceRow}>
            <Text style={[styles.confidenceLabel, { color: C.textSecondary }]}>
              Confidence:{' '}
              <Text style={{ color: confColor, fontWeight: '700' }}>{prediction.confidence}%</Text>
            </Text>
          </View>
          <View style={[styles.bar, { backgroundColor: C.border }]}>
            <View
              style={[styles.fill, { width: `${prediction.confidence}%` as any, backgroundColor: confColor }]}
            />
          </View>
        </View>

        <View style={[styles.lockCta, { borderTopColor: C.border }]}>
          <View style={[styles.lockIconWrap, { backgroundColor: C.primaryLight }]}>
            <Ionicons name="lock-closed" size={16} color={C.primary} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={[styles.lockLabel, { color: C.text }]}>Locked Prediction</Text>
            <Text style={[styles.lockSub, { color: C.textSecondary }]}>
              Upgrade to {nextTier} to unlock
            </Text>
          </View>
          <View style={[styles.upgradePill, { backgroundColor: C.primary }]}>
            <Text style={styles.upgradePillText}>Upgrade</Text>
          </View>
        </View>
      </View>
    );
  }

  return (
    <View style={[styles.card, { backgroundColor: C.card, borderColor: C.border }]}>
      <View style={styles.header}>
        <Text style={[styles.league, { color: C.primary, backgroundColor: C.primaryLight }]}>
          {prediction.league}
        </Text>
        <Text style={[styles.time, { color: C.textSecondary }]}>{formatKickoff(prediction.kickoff_time)}</Text>
      </View>
      <Text style={[styles.matchTitle, { color: C.text }]}>{prediction.match_title}</Text>
      <View style={styles.row}>
        <View style={[styles.predBadge, { backgroundColor: C.primaryLight }]}>
          <Text style={[styles.predText, { color: C.primary }]}>{prediction.prediction}</Text>
        </View>
        <Text style={[styles.odds, { color: C.textSecondary }]}>Odds: {prediction.odds}</Text>
      </View>
      <View style={styles.confidenceRow}>
        <Text style={[styles.confidenceLabel, { color: C.textSecondary }]}>
          Confidence:{' '}
          <Text style={{ color: confColor, fontWeight: '700' }}>{prediction.confidence}%</Text>
        </Text>
      </View>
      <View style={[styles.bar, { backgroundColor: C.border }]}>
        <View
          style={[styles.fill, { width: `${prediction.confidence}%` as any, backgroundColor: confColor }]}
        />
      </View>
      {prediction.reasoning && (
        <Text style={[styles.reasoning, { color: C.textSecondary }]} numberOfLines={2}>
          {prediction.reasoning}
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: 16,
    borderWidth: 1,
    padding: 16,
    marginBottom: 12,
    overflow: 'hidden',
  },
  lockedCard: {
    opacity: 0.92,
  },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  league: {
    fontSize: 11,
    fontWeight: '700',
    paddingHorizontal: 10,
    paddingVertical: 3,
    borderRadius: 20,
    overflow: 'hidden',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  time: { fontSize: 13 },
  matchTitle: { fontSize: 16, fontWeight: '700', marginBottom: 10, lineHeight: 22 },
  blurredSection: { marginBottom: 4 },
  redactedBlock: { borderRadius: 4, marginVertical: 2 },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 },
  predBadge: { paddingHorizontal: 12, paddingVertical: 5, borderRadius: 8 },
  predText: { fontWeight: '700', fontSize: 14 },
  odds: { fontSize: 14 },
  confidenceRow: { marginBottom: 6 },
  confidenceLabel: { fontSize: 13 },
  bar: { height: 6, borderRadius: 3, overflow: 'hidden', marginBottom: 10 },
  fill: { height: '100%', borderRadius: 3 },
  reasoning: { fontSize: 13, lineHeight: 18, marginTop: 4 },
  lockCta: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    borderTopWidth: 1,
    paddingTop: 12,
    marginTop: 8,
  },
  lockIconWrap: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  lockLabel: { fontSize: 13, fontFamily: 'Inter_600SemiBold' },
  lockSub: { fontSize: 12, fontFamily: 'Inter_400Regular', marginTop: 1 },
  upgradePill: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 20,
  },
  upgradePillText: { color: '#fff', fontSize: 12, fontWeight: '700' },
});
