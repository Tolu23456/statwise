import React, { useState } from 'react';
import { View, Text, StyleSheet, Platform, TouchableOpacity, Image, Share } from 'react-native';
import Ionicons from '@expo/vector-icons/Ionicons';
import { useRouter } from 'expo-router';
import { Colors } from '@/constants/colors';
import { Prediction } from '@/lib/supabase';
import { useTheme } from '@/context/ThemeContext';
import { getTeamLogo, getMatchStatus } from '@/lib/teamLogos';
import { shareText } from '@/lib/favorites';
import {
  formatKickoff,
  formatClock,
  isLive,
  hasStarted,
  formatEdge,
  countdownLabel,
} from '@/lib/format';
import { getLiveState, liveChip, formatMinute, formatScore } from '@/lib/liveFeed';

type Props = {
  prediction: Prediction;
  locked?: boolean;
  nextTier?: string;
  onPress?: () => void;
  /** Plan 3.5 — quick actions (bookmark state is owned by the screen). */
  favorite?: boolean;
  onToggleFavorite?: () => void;
};

function getConfidenceColor(confidence: number, C: typeof Colors.dark) {
  if (confidence >= 75) return C.success;
  if (confidence >= 50) return C.warning;
  return C.danger;
}

type Outcome = { key: 'H' | 'D' | 'A'; label: string; prob: number };

function buildOutcomes(p: Prediction): Outcome[] | null {
  const s = p.stats;
  if (!s) return null;
  const { prob_home, prob_draw, prob_away } = s;
  if (
    typeof prob_home !== 'number' ||
    typeof prob_draw !== 'number' ||
    typeof prob_away !== 'number'
  ) {
    return null;
  }
  return [
    { key: 'H', label: 'Home', prob: prob_home },
    { key: 'D', label: 'Draw', prob: prob_draw },
    { key: 'A', label: 'Away', prob: prob_away },
  ];
}

/** Map the model's pick onto a short code. */
function pickCode(prediction: string): 'H' | 'D' | 'A' | null {
  if (prediction === 'Home Win') return 'H';
  if (prediction === 'Draw') return 'D';
  if (prediction === 'Away Win') return 'A';
  return null;
}

export function PredictionCard({ prediction, locked = false, nextTier = 'Premium Tier', onPress, favorite = false, onToggleFavorite }: Props) {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const router = useRouter();
  const confColor = getConfidenceColor(prediction.confidence, C);
  // Plan 3.5 — share falls back to the clipboard when the platform has no
  // Web Share API, and says so in place of the tap hint for a couple of
  // seconds (a silent copy would look like a dead button).
  const [copied, setCopied] = useState(false);

  const handleShare = async (e?: { stopPropagation?: () => void }) => {
    e?.stopPropagation?.();
    const text = shareText(prediction);
    try {
      await Share.share({ message: text });
    } catch {
      try {
        if (Platform.OS === 'web' && typeof navigator !== 'undefined' && navigator.clipboard) {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        }
      } catch {
        // nothing to fall back to — the card stays usable, no crash
      }
    }
  };

  const certainty = prediction.stats?.certainty ?? prediction.confidence;
  const isAiPick = prediction.stats?.is_ai_pick === true;
  // Cup competitions with no season CSV of their own are predicted from pooled
  // priors; the model says so rather than implying league-specific knowledge.
  const isReducedConfidence = prediction.stats?.reduced_confidence === true;
  const edge = prediction.stats?.value_edge ?? null;
  const outcomes = buildOutcomes(prediction);
  const pick = pickCode(prediction.prediction);
  const status = getMatchStatus(prediction as any);
  // Plan 2.2 — real live state (stamped every 5 min by ai/live_enricher.py)
  // supersedes the time-based guess while it's fresh (<10 min old).
  const liveState = getLiveState(prediction.stats?.details);
  const liveInfo = liveChip(liveState);
  const liveScoreText = liveInfo?.showScore ? formatScore(liveState?.score ?? null) : null;
  const homeLogo = getTeamLogo(prediction.home_team);
  const awayLogo = getTeamLogo(prediction.away_team);
  const showScore = status.state === 'FT' || status.state === 'HT' || status.live;

  // The top-level `odds` column holds the *home* price. When the market
  // snapshot is available we show the price of the outcome we actually picked.
  const s = prediction.stats;
  const pickOdds =
    pick === 'H' ? s?.odds_home : pick === 'D' ? s?.odds_draw : pick === 'A' ? s?.odds_away : null;
  const displayOdds = pickOdds ?? (pick === 'H' ? prediction.odds : null);
  const oddsLabel =
    pickOdds != null
      ? pick === 'H' ? 'Home' : pick === 'D' ? 'Draw' : 'Away'
      : 'Home';

  const blurStyle = Platform.OS === 'web'
    ? ({ filter: 'blur(6px)', userSelect: 'none' } as any)
    : { opacity: 0.08 };

  const showValue = !locked && edge !== null && edge >= 3;

  /* ------------------------------ locked card ----------------------------- */
  if (locked) {
    return (
      <View style={[styles.card, styles.lockedCard, { backgroundColor: C.card, borderColor: C.border }]}>
        <View style={styles.header}>
          <View style={styles.leagueWrap}>
            <Text style={[styles.league, { color: C.textMuted, backgroundColor: C.inputBg }]}>
              {prediction.league}
            </Text>
            {(liveInfo?.live ?? status.live) && (
              <View style={[styles.liveChip, { backgroundColor: C.dangerLight }]}>
                <View style={[styles.liveDot, { backgroundColor: C.danger }]} />
                <Text style={[styles.liveText, { color: C.danger }]}>LIVE</Text>
              </View>
            )}
          </View>
          <Text style={[styles.time, { color: C.textSecondary }]}>
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
            <Text style={[styles.odds, { color: C.textSecondary }]}>
              {prediction.odds ? `Odds ${prediction.odds}` : ''}
            </Text>
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
          <TouchableOpacity
            style={[styles.upgradePill, { backgroundColor: C.primary }]}
            onPress={() => router.push('/(tabs)/subscriptions')}
            activeOpacity={0.8}
          >
            <Text style={styles.upgradePillText}>Upgrade</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  /* -------------------------------- card --------------------------------- */
  return (
    <TouchableOpacity
      activeOpacity={0.75}
      onPress={onPress}
      style={[styles.card, { backgroundColor: C.card, borderColor: C.border }]}
    >
        <View style={styles.header}>
          <View style={styles.leagueWrap}>
            <Text style={[styles.league, { color: C.primary, backgroundColor: C.primaryLight }]}>
              {prediction.league}
            </Text>
            {liveInfo ? (
              liveInfo.live ? (
                <View style={[styles.liveChip, { backgroundColor: C.dangerLight }]}>
                  <View style={[styles.liveDot, { backgroundColor: C.danger }]} />
                  <Text style={[styles.liveText, { color: C.danger }]}>
                    LIVE{liveInfo.showMinute ? ` ${formatMinute(liveState?.minute ?? null)}` : ''}
                  </Text>
                </View>
              ) : (
                <View style={[styles.liveChip, { backgroundColor: C.inputBg }]}>
                  <Text style={[styles.liveText, { color: C.textSecondary }]}>FT</Text>
                </View>
              )
            ) : status.state === 'LIVE' ? (
              <View style={[styles.liveChip, { backgroundColor: C.dangerLight }]}>
                <View style={[styles.liveDot, { backgroundColor: C.danger }]} />
                <Text style={[styles.liveText, { color: C.danger }]}>LIVE</Text>
              </View>
            ) : status.state === 'HT' ? (
              <View style={[styles.liveChip, { backgroundColor: C.warningLight }]}>
                <Text style={[styles.liveText, { color: C.warning }]}>HT</Text>
              </View>
            ) : status.state === 'FT' ? (
              <View style={[styles.liveChip, { backgroundColor: C.inputBg }]}>
                <Text style={[styles.liveText, { color: C.textSecondary }]}>FT</Text>
              </View>
            ) : countdownLabel(prediction.kickoff_time) ? (
              <View style={[styles.countdownChip, { backgroundColor: C.inputBg }]}>
                <Text style={[styles.countdownText, { color: C.textSecondary }]}>in {countdownLabel(prediction.kickoff_time)}</Text>
              </View>
            ) : null}
          </View>
          <View style={styles.timeWrap}>
            <Text style={[styles.time, { color: C.textSecondary }]}>{formatKickoff(prediction.kickoff_time)}</Text>
            <Ionicons name="chevron-forward" size={14} color={C.textMuted} />
          </View>
        </View>

        {/* Teams with logos and score */}
        <View style={styles.teamsRow}>
          <View style={[styles.teamCol, { justifyContent: 'flex-start' }]}>
            {homeLogo ? <Image source={{ uri: homeLogo }} style={styles.teamLogo} resizeMode="contain" /> : <View style={[styles.teamLogo, { backgroundColor: C.inputBg, alignItems: 'center', justifyContent: 'center' }]}><Ionicons name="football-outline" size={18} color={C.textMuted} /></View>}
            <Text style={[styles.teamName, { color: C.text, flex: 1 }]} numberOfLines={1} ellipsizeMode="tail">{prediction.home_team}</Text>
          </View>
          <View style={styles.scoreCol}>
            <View style={{ alignItems: 'center' }}>
              {liveScoreText ? (
                <Text style={[styles.scoreText, { color: C.text }]}>{liveScoreText}</Text>
              ) : (status.state === 'FT' || status.state === 'LIVE' || status.state === 'HT') && (prediction.home_score != null && prediction.away_score != null) ? (
                <Text style={[styles.scoreText, { color: C.text }]}>{prediction.home_score} - {prediction.away_score}</Text>
              ) : (
                <Text style={[styles.vsText, { color: C.textMuted }]}>VS</Text>
              )}
              {liveInfo?.live ? (
                liveInfo.showMinute ? (
                  <Text style={[styles.liveTime, { color: C.danger }]}>{formatMinute(liveState?.minute ?? null)}</Text>
                ) : (
                  <Text style={[styles.liveTime, { color: C.danger }]}>LIVE</Text>
                )
              ) : status.state === 'LIVE' && (
                <Text style={[styles.liveTime, { color: C.danger }]}>LIVE</Text>
              )}
              {!liveInfo && status.state === 'SCHEDULED' && (() => {
                const now = Date.now();
                const kickoff = new Date(prediction.kickoff_time).getTime();
                const diff = kickoff - now;
                if (diff > 0 && diff <= 24 * 60 * 60 * 1000) {
                  const hours = Math.floor(diff / (1000 * 60 * 60));
                  const mins = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
                  return <Text style={[styles.countdownText, { color: C.primary, marginTop: 2 }]}>in {hours}h {mins}m</Text>;
                }
                return null;
              })()}
            </View>
          </View>
          <View style={[styles.teamCol, { justifyContent: 'flex-end' }]}>
            <Text style={[styles.teamName, { color: C.text, textAlign: 'right', flex: 1 }]} numberOfLines={1} ellipsizeMode="tail">{prediction.away_team}</Text>
            {awayLogo ? <Image source={{ uri: awayLogo }} style={styles.teamLogo} resizeMode="contain" /> : <View style={[styles.teamLogo, { backgroundColor: C.inputBg, alignItems: 'center', justifyContent: 'center' }]}><Ionicons name="football-outline" size={18} color={C.textMuted} /></View>}
          </View>
        </View>

      <View style={styles.row}>
        <View style={styles.badgeRow}>
          <View style={[styles.predBadge, { backgroundColor: C.primaryLight }]}>
            <Text style={[styles.predText, { color: C.primary }]}>{prediction.prediction}</Text>
          </View>
          {isAiPick && (
            <View style={[styles.aiPickBadge, { backgroundColor: C.successLight, borderColor: C.success }]}>
              <Ionicons name="star" size={12} color={C.success} />
              <Text style={[styles.aiPickText, { color: C.success }]}>AI Pick</Text>
            </View>
          )}
          {showValue && (
            <View style={[styles.valueBadge, { backgroundColor: C.goldLight, borderColor: C.gold }]}>
              <Ionicons name="trending-up" size={12} color={C.gold} />
              <Text style={[styles.valueText, { color: C.gold }]}>
                Value {formatEdge(edge)}
              </Text>
            </View>
          )}
          {isReducedConfidence && (
            <View style={[styles.aiPickBadge, { backgroundColor: C.warningLight, borderColor: C.warning }]}>
              <Ionicons name="information-circle-outline" size={12} color={C.warning} />
              <Text style={[styles.aiPickText, { color: C.warning }]}>Pooled data</Text>
            </View>
          )}
          {/* Fixture source tags ("ESPN" / "football-data" badges) removed
              per user requirement — the data source is an implementation
              detail, not something to surface on cards. */}
        </View>
        {displayOdds ? (
          <Text style={[styles.odds, { color: C.textSecondary }]}>
            {oddsLabel}{' '}
            <Text style={{ color: C.text, fontWeight: '700' }}>{displayOdds}</Text>
          </Text>
        ) : null}
      </View>

      {/* Three-way probability breakdown */}
      {outcomes && (
        <View style={styles.probBlock}>
          <Text style={[styles.probCaption, { color: C.textMuted }]}>
            Pico&apos;s win probability
          </Text>
          <View style={styles.probBars}>
            {outcomes.map(o => {
              const isPick = o.key === pick;
              const barColor = isPick ? C.primary : C.border;
              return (
                <View key={o.key} style={styles.probCol}>
                  <Text
                    style={[
                      styles.probValue,
                      { color: isPick ? C.primary : C.textSecondary },
                      isPick && { fontWeight: '700' },
                    ]}
                  >
                    {Math.round(o.prob)}%
                  </Text>
                  <View style={[styles.probTrack, { backgroundColor: C.inputBg }]}>
                    <View
                      style={[
                        styles.probFill,
                        { width: `${Math.max(2, Math.min(100, o.prob))}%` as any, backgroundColor: barColor },
                      ]}
                    />
                  </View>
                  <Text
                    style={[
                      styles.probLabel,
                      { color: isPick ? C.primary : C.textMuted },
                      isPick && { fontWeight: '700' },
                    ]}
                  >
                    {o.label}
                  </Text>
                </View>
              );
            })}
          </View>
        </View>
      )}

      <View style={styles.confidenceRow}>
        <Text style={[styles.confidenceLabel, { color: C.textSecondary }]}>
          Confidence{' '}
          <Text style={{ color: confColor, fontWeight: '700' }}>{prediction.confidence}%</Text>
        </Text>
        {typeof certainty === 'number' && (
          <View style={styles.certaintyWrap}>
            <Ionicons name="shield-checkmark" size={12} color={C.primary} />
            <Text style={[styles.certaintyLabel, { color: C.textMuted }]}>
              Certainty{' '}
              <Text style={{ color: C.primary, fontWeight: '700' }}>{Math.round(certainty)}%</Text>
            </Text>
          </View>
        )}
      </View>

      {prediction.reasoning && (
        <Text style={[styles.reasoning, { color: C.textSecondary }]} numberOfLines={2}>
          {prediction.reasoning}
        </Text>
      )}

      <View style={[styles.tapHint, { borderTopColor: C.border }]}>
        <Ionicons name="analytics-outline" size={13} color={C.textMuted} />
        <Text style={[styles.tapHintText, { color: copied ? C.primary : C.textMuted }]}>
          {copied ? 'Copied to clipboard' : 'Tap for full analysis'}
        </Text>
        {hasStarted(prediction.kickoff_time) && !status.live && status.state !== 'FT' && (
          <Text style={[styles.kickedOff, { color: C.textMuted }]}>Kicked off {formatClock(prediction.kickoff_time)}</Text>
        )}
        <View style={styles.quickActions}>
          <TouchableOpacity
            style={styles.quickBtn}
            onPress={handleShare}
            hitSlop={8}
            accessibilityRole="button"
            accessibilityLabel="Share this pick"
          >
            <Ionicons name="share-outline" size={16} color={copied ? C.primary : C.textMuted} />
          </TouchableOpacity>
          {onToggleFavorite && (
            <TouchableOpacity
              style={styles.quickBtn}
              onPress={e => {
                e.stopPropagation?.();
                onToggleFavorite();
              }}
              hitSlop={8}
              accessibilityRole="button"
              accessibilityLabel={favorite ? 'Remove from favourites' : 'Save to favourites'}
            >
              <Ionicons
                name={favorite ? 'bookmark' : 'bookmark-outline'}
                size={16}
                color={favorite ? C.primary : C.textMuted}
              />
            </TouchableOpacity>
          )}
        </View>
      </View>
    </TouchableOpacity>
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
  lockedCard: { opacity: 0.92 },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 },
  leagueWrap: { flexDirection: 'row', alignItems: 'center', gap: 6, flexShrink: 1 },
  timeWrap: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  league: {
    fontSize: 11, fontWeight: '700', paddingHorizontal: 10, paddingVertical: 3,
    borderRadius: 20, overflow: 'hidden', textTransform: 'uppercase', letterSpacing: 0.5,
  },
  liveChip: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: 7, paddingVertical: 3, borderRadius: 20,
  },
  liveDot: { width: 6, height: 6, borderRadius: 3 },
  liveText: { fontSize: 10, fontWeight: '800', letterSpacing: 0.5 },
  countdownChip: { paddingHorizontal: 7, paddingVertical: 3, borderRadius: 20 },
  countdownText: { fontSize: 11, fontWeight: '600' },
  time: { fontSize: 12, fontWeight: '500' },
  teamsRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, gap: 8 },
  teamCol: { flexDirection: 'row', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 },
  teamLogo: { width: 32, height: 32, borderRadius: 16, backgroundColor: 'transparent' },
  teamName: { fontSize: 13, fontWeight: '600', flexShrink: 1 },
  scoreCol: { alignItems: 'center', paddingHorizontal: 8, minWidth: 60 },
  scoreText: { fontSize: 18, fontWeight: '800' },
  vsText: { fontSize: 12, fontWeight: '700' },
  liveTime: { fontSize: 9, fontWeight: '700', marginTop: 2 },
  matchTitle: { fontSize: 16, fontWeight: '700', marginBottom: 10, lineHeight: 22 },
  blurredSection: { marginBottom: 4 },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 },
  badgeRow: { flexDirection: 'row', alignItems: 'center', gap: 6, flexShrink: 1, flexWrap: 'wrap' },
  predBadge: { paddingHorizontal: 12, paddingVertical: 5, borderRadius: 8 },
  predText: { fontWeight: '700', fontSize: 14 },
  odds: { fontSize: 13 },
  probBlock: { marginBottom: 10 },
  probCaption: {
    fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.6,
    marginBottom: 6, fontWeight: '700',
  },
  probBars: { flexDirection: 'row', gap: 8 },
  probCol: { flex: 1, alignItems: 'center' },
  probValue: { fontSize: 13, marginBottom: 4 },
  probTrack: { height: 5, borderRadius: 3, overflow: 'hidden', width: '100%' },
  probFill: { height: '100%', borderRadius: 3 },
  probLabel: { fontSize: 10, marginTop: 4, textTransform: 'uppercase', letterSpacing: 0.4 },
  confidenceRow: { marginBottom: 6, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  confidenceLabel: { fontSize: 13 },
  certaintyWrap: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  certaintyLabel: { fontSize: 12 },
  bar: { height: 6, borderRadius: 3, overflow: 'hidden', marginBottom: 10 },
  fill: { height: '100%', borderRadius: 3 },
  reasoning: { fontSize: 13, lineHeight: 18, marginTop: 2, marginBottom: 8 },
  tapHint: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    borderTopWidth: 1, paddingTop: 10, marginTop: 2,
  },
  tapHintText: { fontSize: 12, flex: 1 },
  kickedOff: { fontSize: 11 },
  quickActions: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  quickBtn: { padding: 2 },
  lockCta: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    borderTopWidth: 1, paddingTop: 12, marginTop: 8,
  },
  lockIconWrap: {
    width: 32, height: 32, borderRadius: 16,
    alignItems: 'center', justifyContent: 'center',
  },
  lockLabel: { fontSize: 13, fontFamily: 'Inter_600SemiBold' },
  lockSub: { fontSize: 12, fontFamily: 'Inter_400Regular', marginTop: 1 },
  upgradePill: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 20 },
  upgradePillText: { color: '#fff', fontSize: 12, fontWeight: '700' },
  aiPickBadge: { flexDirection: 'row', alignItems: 'center', gap: 3, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 20, borderWidth: 1 },
  aiPickText: { fontSize: 11, fontWeight: '700' },
  valueBadge: { flexDirection: 'row', alignItems: 'center', gap: 3, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 20, borderWidth: 1 },
  valueText: { fontSize: 11, fontWeight: '700' },
});
