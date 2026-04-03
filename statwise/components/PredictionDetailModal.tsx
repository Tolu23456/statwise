import React from 'react';
import {
  Modal, View, Text, StyleSheet, ScrollView,
  TouchableOpacity, Platform, Dimensions,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Colors } from '@/constants/colors';
import { Prediction } from '@/lib/supabase';
import { useTheme } from '@/context/ThemeContext';

type Props = {
  prediction: Prediction | null;
  visible: boolean;
  onClose: () => void;
};

function formatKickoff(raw: string): string {
  try {
    const d = new Date(raw);
    if (isNaN(d.getTime())) return raw;
    return d.toLocaleString([], {
      weekday: 'short', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch { return raw; }
}

function getConfidenceColor(confidence: number, C: typeof Colors.dark) {
  if (confidence >= 75) return C.success;
  if (confidence >= 50) return C.warning;
  return C.danger;
}

function ProbBar({
  label, value, color, C,
}: {
  label: string; value: number; color: string; C: typeof Colors.dark;
}) {
  return (
    <View style={{ marginBottom: 10 }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 }}>
        <Text style={{ color: C.textSecondary, fontSize: 13 }}>{label}</Text>
        <Text style={{ color, fontSize: 13, fontWeight: '700' }}>{value.toFixed(0)}%</Text>
      </View>
      <View style={[probStyles.track, { backgroundColor: C.border }]}>
        <View style={[probStyles.fill, { width: `${Math.min(value, 100)}%` as any, backgroundColor: color }]} />
      </View>
    </View>
  );
}

function OddsRow({ label, value, highlight, C }: {
  label: string; value?: number | null; highlight?: boolean; C: typeof Colors.dark;
}) {
  if (!value) return null;
  return (
    <View style={[
      probStyles.oddsRow,
      { backgroundColor: highlight ? C.primaryLight : C.inputBg, borderColor: C.border },
    ]}>
      <Text style={{ color: C.textSecondary, fontSize: 13 }}>{label}</Text>
      <Text style={{ color: highlight ? C.primary : C.text, fontWeight: '700', fontSize: 14 }}>
        {value.toFixed(2)}
      </Text>
    </View>
  );
}

export function PredictionDetailModal({ prediction, visible, onClose }: Props) {
  const { scheme } = useTheme();
  const C = Colors[scheme];

  if (!prediction) return null;

  const confColor = getConfidenceColor(prediction.confidence, C);

  const impliedHome = prediction.odds_home ? (1 / prediction.odds_home) * 100 : null;
  const impliedDraw = prediction.odds_draw ? (1 / prediction.odds_draw) * 100 : null;
  const impliedAway = prediction.odds_away ? (1 / prediction.odds_away) * 100 : null;
  const overround = (impliedHome ?? 0) + (impliedDraw ?? 0) + (impliedAway ?? 0);
  const pHome = impliedHome && overround > 0 ? (impliedHome / overround) * 100 : null;
  const pDraw = impliedDraw && overround > 0 ? (impliedDraw / overround) * 100 : null;
  const pAway = impliedAway && overround > 0 ? (impliedAway / overround) * 100 : null;

  const predLabel = (prediction.prediction ?? '').toLowerCase();
  const isHome = predLabel.includes('home') || predLabel.includes(prediction.home_team?.toLowerCase() ?? '');
  const isDraw = predLabel.includes('draw');
  const isAway = predLabel.includes('away') || predLabel.includes(prediction.away_team?.toLowerCase() ?? '');

  return (
    <Modal
      visible={visible}
      animationType="slide"
      transparent
      onRequestClose={onClose}
      statusBarTranslucent
    >
      <View style={styles.overlay}>
        <TouchableOpacity style={styles.backdrop} activeOpacity={1} onPress={onClose} />

        <View style={[styles.sheet, { backgroundColor: C.card }]}>
          <View style={[styles.handle, { backgroundColor: C.border }]} />

          <ScrollView
            showsVerticalScrollIndicator={false}
            contentContainerStyle={styles.content}
            bounces={Platform.OS !== 'web'}
          >
            {/* ── Header ── */}
            <View style={styles.headerRow}>
              <View style={{ flex: 1 }}>
                <Text style={[styles.leagueTag, { color: C.primary, backgroundColor: C.primaryLight }]}>
                  {prediction.league}
                </Text>
                <Text style={[styles.matchTitle, { color: C.text }]}>
                  {prediction.match_title || `${prediction.home_team} vs ${prediction.away_team}`}
                </Text>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 4 }}>
                  <Ionicons name="time-outline" size={13} color={C.textMuted} />
                  <Text style={{ color: C.textMuted, fontSize: 13 }}>
                    {formatKickoff(prediction.kickoff_time)}
                  </Text>
                </View>
              </View>
              <TouchableOpacity
                onPress={onClose}
                style={[styles.closeBtn, { backgroundColor: C.inputBg }]}
              >
                <Ionicons name="close" size={18} color={C.textSecondary} />
              </TouchableOpacity>
            </View>

            {/* ── AI Pick ── */}
            <View style={[styles.pickCard, { backgroundColor: C.primaryLight, borderColor: C.primary }]}>
              <View style={styles.pickRow}>
                <Ionicons name="hardware-chip-outline" size={16} color={C.primary} />
                <Text style={[styles.pickLabel, { color: C.primary }]}>AI Prediction</Text>
              </View>
              <Text style={[styles.pickValue, { color: C.primary }]}>{prediction.prediction}</Text>
            </View>

            {/* ── Confidence ── */}
            <View style={[styles.section, { backgroundColor: C.background, borderColor: C.border }]}>
              <Text style={[styles.sectionTitle, { color: C.text }]}>Confidence</Text>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 8 }}>
                <View style={[styles.confCircle, { borderColor: confColor }]}>
                  <Text style={[styles.confNum, { color: confColor }]}>{prediction.confidence}%</Text>
                </View>
                <View style={{ flex: 1 }}>
                  <View style={[styles.confTrack, { backgroundColor: C.border }]}>
                    <View
                      style={[styles.confFill, {
                        width: `${prediction.confidence}%` as any,
                        backgroundColor: confColor,
                      }]}
                    />
                  </View>
                  <Text style={{ color: C.textSecondary, fontSize: 12, marginTop: 6 }}>
                    {prediction.confidence >= 75 ? 'High confidence — strong signal'
                      : prediction.confidence >= 50 ? 'Medium confidence — moderate signal'
                      : 'Low confidence — use with caution'}
                  </Text>
                </View>
              </View>
            </View>

            {/* ── Win Probability Breakdown ── */}
            {(pHome !== null || pDraw !== null || pAway !== null) && (
              <View style={[styles.section, { backgroundColor: C.background, borderColor: C.border }]}>
                <Text style={[styles.sectionTitle, { color: C.text }]}>Win Probability</Text>
                <View style={{ marginTop: 8 }}>
                  {pHome !== null && (
                    <ProbBar
                      label={`${prediction.home_team} Win`}
                      value={pHome}
                      color={isHome ? C.primary : C.textSecondary}
                      C={C}
                    />
                  )}
                  {pDraw !== null && (
                    <ProbBar
                      label="Draw"
                      value={pDraw}
                      color={isDraw ? C.warning : C.textSecondary}
                      C={C}
                    />
                  )}
                  {pAway !== null && (
                    <ProbBar
                      label={`${prediction.away_team} Win`}
                      value={pAway}
                      color={isAway ? C.danger : C.textSecondary}
                      C={C}
                    />
                  )}
                </View>
              </View>
            )}

            {/* ── Odds ── */}
            {(prediction.odds_home || prediction.odds_draw || prediction.odds_away || prediction.odds) && (
              <View style={[styles.section, { backgroundColor: C.background, borderColor: C.border }]}>
                <Text style={[styles.sectionTitle, { color: C.text }]}>Odds</Text>
                <View style={{ gap: 8, marginTop: 8 }}>
                  <OddsRow label={`${prediction.home_team} Win`} value={prediction.odds_home} highlight={isHome} C={C} />
                  <OddsRow label="Draw" value={prediction.odds_draw} highlight={isDraw} C={C} />
                  <OddsRow label={`${prediction.away_team} Win`} value={prediction.odds_away} highlight={isAway} C={C} />
                  {!prediction.odds_home && prediction.odds && (
                    <OddsRow label="Suggested Odds" value={prediction.odds} highlight C={C} />
                  )}
                </View>
              </View>
            )}

            {/* ── AI Reasoning ── */}
            {prediction.reasoning && (
              <View style={[styles.section, { backgroundColor: C.background, borderColor: C.border }]}>
                <View style={styles.reasoningHeader}>
                  <Ionicons name="bulb-outline" size={15} color={C.warning} />
                  <Text style={[styles.sectionTitle, { color: C.text }]}>AI Analysis</Text>
                </View>
                <Text style={[styles.reasoningText, { color: C.textSecondary }]}>
                  {prediction.reasoning}
                </Text>
              </View>
            )}

            {/* ── Disclaimer ── */}
            <Text style={[styles.disclaimer, { color: C.textMuted }]}>
              AI predictions are for informational purposes only. Gamble responsibly.
            </Text>
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const { height } = Dimensions.get('window');

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    justifyContent: 'flex-end',
  },
  backdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0,0,0,0.5)',
  },
  sheet: {
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    maxHeight: height * 0.88,
    paddingBottom: Platform.OS === 'ios' ? 34 : 16,
    overflow: 'hidden',
  },
  handle: {
    width: 40, height: 4, borderRadius: 2,
    alignSelf: 'center', marginTop: 12, marginBottom: 4,
  },
  content: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 24, gap: 14 },
  headerRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  leagueTag: {
    fontSize: 10, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.6,
    paddingHorizontal: 10, paddingVertical: 3, borderRadius: 20, alignSelf: 'flex-start',
    marginBottom: 6, overflow: 'hidden',
  },
  matchTitle: { fontSize: 18, fontWeight: '700', lineHeight: 24 },
  closeBtn: {
    width: 34, height: 34, borderRadius: 17,
    alignItems: 'center', justifyContent: 'center', marginTop: 2,
  },
  pickCard: {
    borderRadius: 14, borderWidth: 1.5, padding: 16,
  },
  pickRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 6 },
  pickLabel: { fontSize: 12, fontWeight: '600', textTransform: 'uppercase', letterSpacing: 0.5 },
  pickValue: { fontSize: 22, fontWeight: '800' },
  section: {
    borderRadius: 14, borderWidth: 1, padding: 14,
  },
  sectionTitle: { fontSize: 14, fontWeight: '700' },
  confCircle: {
    width: 60, height: 60, borderRadius: 30, borderWidth: 3,
    alignItems: 'center', justifyContent: 'center',
  },
  confNum: { fontSize: 16, fontWeight: '800' },
  confTrack: { height: 8, borderRadius: 4, overflow: 'hidden' },
  confFill: { height: '100%', borderRadius: 4 },
  reasoningHeader: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 8 },
  reasoningText: { fontSize: 14, lineHeight: 21 },
  disclaimer: { fontSize: 11, textAlign: 'center', paddingTop: 4 },
});

const probStyles = StyleSheet.create({
  track: { height: 6, borderRadius: 3, overflow: 'hidden' },
  fill: { height: '100%', borderRadius: 3 },
  oddsRow: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1,
  },
});
