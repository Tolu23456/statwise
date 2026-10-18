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

function FormDot({ val, C }: { val: number; C: typeof Colors.dark }) {
  const color = val === 1 ? C.success : val === 0.5 ? C.warning : C.danger;
  const label = val === 1 ? 'W' : val === 0.5 ? 'D' : 'L';
  return (
    <View style={[styles.formDot, { backgroundColor: color }]}>
      <Text style={styles.formDotText}>{label}</Text>
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

  const stats = prediction.stats;
  const details = stats?.details;

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

            {/* ── Match Statistics (NEW) ── */}
            {stats && (
              <View style={[styles.section, { backgroundColor: C.background, borderColor: C.border }]}>
                <Text style={[styles.sectionTitle, { color: C.text, marginBottom: 12 }]}>Match Insights</Text>

                {/* H2H Breakdown */}
                <View style={{ marginBottom: 16 }}>
                  <Text style={{ color: C.textSecondary, fontSize: 12, marginBottom: 6, textTransform: 'uppercase' }}>Head-to-Head History</Text>
                  <View style={styles.h2hRow}>
                    <View style={[styles.h2hBar, { flex: stats.h2h.home_wins, backgroundColor: C.primary }]}>
                      <Text style={styles.h2hText}>{stats.h2h.home_wins}</Text>
                    </View>
                    <View style={[styles.h2hBar, { flex: stats.h2h.draws, backgroundColor: C.textMuted }]}>
                      <Text style={styles.h2hText}>{stats.h2h.draws}</Text>
                    </View>
                    <View style={[styles.h2hBar, { flex: stats.h2h.away_wins, backgroundColor: C.danger }]}>
                      <Text style={styles.h2hText}>{stats.h2h.away_wins}</Text>
                    </View>
                  </View>
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 4 }}>
                    <Text style={{ color: C.textMuted, fontSize: 10 }}>Home Wins</Text>
                    <Text style={{ color: C.textMuted, fontSize: 10 }}>Draws</Text>
                    <Text style={{ color: C.textMuted, fontSize: 10 }}>Away Wins</Text>
                  </View>
                </View>

                {/* Form Comparison */}
                <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                  <View style={{ flex: 1 }}>
                    <Text style={{ color: C.textSecondary, fontSize: 12, marginBottom: 6 }}>Home Form</Text>
                    <View style={{ flexDirection: 'row', gap: 4 }}>
                      {stats.home_form.map((v, i) => <FormDot key={i} val={v} C={C} />)}
                    </View>
                  </View>
                  <View style={{ flex: 1, alignItems: 'flex-end' }}>
                    <Text style={{ color: C.textSecondary, fontSize: 12, marginBottom: 6 }}>Away Form</Text>
                    <View style={{ flexDirection: 'row', gap: 4 }}>
                      {stats.away_form.map((v, i) => <FormDot key={i} val={v} C={C} />)}
                    </View>
                  </View>
                </View>

                <View style={{ marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: C.border }}>
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 8 }}>
                    <Text style={{ color: C.textSecondary, fontSize: 12 }}>Avg. Match Goals</Text>
                    <Text style={{ color: C.text, fontWeight: '700', fontSize: 12 }}>{stats.avg_goals.toFixed(2)}</Text>
                  </View>

                  {stats.home_value !== undefined && (
                    <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 8 }}>
                      <Text style={{ color: C.textSecondary, fontSize: 12 }}>Squad Market Value</Text>
                      <Text style={{ color: C.text, fontWeight: '700', fontSize: 12 }}>
                        €{(stats.home_value / 1e6).toFixed(1)}M vs €{(stats.away_value! / 1e6).toFixed(1)}M
                      </Text>
                    </View>
                  )}

                  {stats.home_rating !== undefined && (
                    <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                      <Text style={{ color: C.textSecondary, fontSize: 12 }}>Squad Quality Rating</Text>
                      <Text style={{ color: C.text, fontWeight: '700', fontSize: 12 }}>
                        {stats.home_rating.toFixed(1)} vs {stats.away_rating!.toFixed(1)}
                      </Text>
                    </View>
                  )}
                </View>
              </View>
            )}

            {/* ── Match Details: venue, weather, attendance ── */}
            {details && (details.venue || details.weather || details.attendance) && (
              <View style={[styles.section, { backgroundColor: C.background, borderColor: C.border }]}>
                <Text style={[styles.sectionTitle, { color: C.text, marginBottom: 12 }]}>Match Info</Text>

                {details.venue && (
                  <View style={styles.infoRow}>
                    <Ionicons name="location-outline" size={15} color={C.textSecondary} />
                    <View style={{ flex: 1 }}>
                      <Text style={[styles.infoLabel, { color: C.text }]}>{details.venue}</Text>
                      {(details.city || details.country) && (
                        <Text style={[styles.infoSub, { color: C.textMuted }]}>
                          {[details.city, details.country].filter(Boolean).join(', ')}
                        </Text>
                      )}
                    </View>
                  </View>
                )}

                {details.attendance != null && (
                  <View style={styles.infoRow}>
                    <Ionicons name="people-outline" size={15} color={C.textSecondary} />
                    <Text style={[styles.infoLabel, { color: C.text }]}>
                      {details.attendance.toLocaleString()} attendance
                    </Text>
                  </View>
                )}

                {details.referee && (
                  <View style={styles.infoRow}>
                    <Ionicons name="person-outline" size={15} color={C.textSecondary} />
                    <Text style={[styles.infoLabel, { color: C.text }]}>Ref: {details.referee}</Text>
                  </View>
                )}

                {details.weather && (
                  <View style={[styles.weatherBox, { backgroundColor: C.inputBg, borderColor: C.border }]}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                      <Ionicons name="partly-sunny-outline" size={16} color={C.warning} />
                      <Text style={{ color: C.text, fontWeight: '700', fontSize: 13 }}>
                        {details.weather.temperature_c != null ? `${details.weather.temperature_c.toFixed(0)}°C` : '—'}
                        {details.weather.condition ? ` · ${details.weather.condition}` : ''}
                      </Text>
                    </View>
                    <View style={{ flexDirection: 'row', gap: 14, marginTop: 6 }}>
                      {details.weather.wind_kmh != null && (
                        <Text style={{ color: C.textSecondary, fontSize: 12 }}>
                          💨 {details.weather.wind_kmh.toFixed(0)} km/h
                        </Text>
                      )}
                      {details.weather.humidity_pct != null && (
                        <Text style={{ color: C.textSecondary, fontSize: 12 }}>
                          💧 {details.weather.humidity_pct.toFixed(0)}%
                        </Text>
                      )}
                      {details.weather.precipitation_mm != null && (
                        <Text style={{ color: C.textSecondary, fontSize: 12 }}>
                          🌧 {details.weather.precipitation_mm.toFixed(1)} mm
                        </Text>
                      )}
                    </View>
                  </View>
                )}
              </View>
            )}

            {/* ── Goal Scorers ── */}
            {details?.scorers && details.scorers.length > 0 && (
              <View style={[styles.section, { backgroundColor: C.background, borderColor: C.border }]}>
                <Text style={[styles.sectionTitle, { color: C.text, marginBottom: 12 }]}>Goal Scorers</Text>
                {details.scorers.map((s, i) => (
                  <View key={i} style={styles.scorerRow}>
                    <Text style={[styles.scorerMinute, { color: C.primary }]}>{s.minute}</Text>
                    <View style={{ flex: 1 }}>
                      <Text style={{ color: C.text, fontSize: 13, fontWeight: '600' }}>{s.player}</Text>
                      <Text style={{ color: C.textMuted, fontSize: 11 }}>{s.team}</Text>
                    </View>
                    <Ionicons name="football-outline" size={14} color={C.success} />
                  </View>
                ))}
              </View>
            )}

            {/* ── Cards ── */}
            {details?.cards && details.cards.length > 0 && (
              <View style={[styles.section, { backgroundColor: C.background, borderColor: C.border }]}>
                <Text style={[styles.sectionTitle, { color: C.text, marginBottom: 12 }]}>Cards</Text>
                {details.cards.map((c, i) => (
                  <View key={i} style={styles.scorerRow}>
                    <Text style={[styles.scorerMinute, { color: C.textSecondary }]}>{c.minute}</Text>
                    <View style={{ flex: 1 }}>
                      <Text style={{ color: C.text, fontSize: 13, fontWeight: '600' }}>{c.player}</Text>
                      <Text style={{ color: C.textMuted, fontSize: 11 }}>{c.team}</Text>
                    </View>
                    <View style={[
                      styles.cardIcon,
                      { backgroundColor: c.type === 'red_card' ? C.dangerLight : c.type === 'yellow_card' ? '#FFD60033' : C.inputBg },
                    ]}>
                      <View style={{
                        width: 8, height: 11, borderRadius: 2,
                        backgroundColor: c.type === 'red_card' ? C.danger : c.type === 'yellow_card' ? '#FFC107' : C.textMuted,
                      }} />
                    </View>
                  </View>
                ))}
              </View>
            )}

            {/* ── Match Stats comparison (completed matches) ── */}
            {details?.team_stats && Object.keys(details.team_stats).length === 2 && (
              <View style={[styles.section, { backgroundColor: C.background, borderColor: C.border }]}>
                <Text style={[styles.sectionTitle, { color: C.text, marginBottom: 12 }]}>Match Statistics</Text>
                {(() => {
                  const [t1, t2] = Object.keys(details.team_stats!);
                  const s1 = details.team_stats![t1];
                  const s2 = details.team_stats![t2];
                  const rows: [string, string, string][] = [
                    ['Possession %', s1['possession'] ?? s1['Possession'] ?? '—', s2['possession'] ?? s2['Possession'] ?? '—'],
                    ['Shots', s1['shots'] ?? '—', s2['shots'] ?? '—'],
                    ['On Target', s1['on_goal'] ?? '—', s2['on_goal'] ?? '—'],
                    ['Offsides', s1['offsides'] ?? '—', s2['offsides'] ?? '—'],
                    ['Corner Kicks', s1['corner_kicks'] ?? '—', s2['corner_kicks'] ?? '—'],
                    ['Fouls', s1['fouls'] ?? '—', s2['fouls'] ?? '—'],
                    ['Yellow Cards', s1['yellow_cards'] ?? '—', s2['yellow_cards'] ?? '—'],
                    ['Saves', s1['saves'] ?? '—', s2['saves'] ?? '—'],
                  ];
                  return (
                    <>
                      <View style={styles.statHeaderRow}>
                        <Text style={[styles.statTeamName, { color: C.text, textAlign: 'left' }]} numberOfLines={1}>{t1}</Text>
                        <Text style={{ color: C.textMuted, fontSize: 10 }}>STAT</Text>
                        <Text style={[styles.statTeamName, { color: C.text, textAlign: 'right' }]} numberOfLines={1}>{t2}</Text>
                      </View>
                      {rows.map(([label, v1, v2]) => (
                        <View key={label} style={styles.statRow}>
                          <Text style={[styles.statValue, { color: C.primary, textAlign: 'left' }]}>{v1}</Text>
                          <Text style={[styles.statLabel, { color: C.textSecondary }]}>{label}</Text>
                          <Text style={[styles.statValue, { color: C.danger, textAlign: 'right' }]}>{v2}</Text>
                        </View>
                      ))}
                    </>
                  );
                })()}
              </View>
            )}

            {/* ── Lineups ── */}
            {details?.lineups && Object.keys(details.lineups).length > 0 && (
              <View style={[styles.section, { backgroundColor: C.background, borderColor: C.border }]}>
                <Text style={[styles.sectionTitle, { color: C.text, marginBottom: 12 }]}>Lineups</Text>
                {Object.entries(details.lineups).map(([team, lu]) => (
                  lu.starting.length > 0 && (
                    <View key={team} style={{ marginBottom: 14 }}>
                      <Text style={{ color: C.textSecondary, fontSize: 12, fontWeight: '700', marginBottom: 6 }}>{team}</Text>
                      {lu.starting.map((p, i) => (
                        <View key={i} style={styles.lineupRow}>
                          <Text style={[styles.lineupJersey, { color: C.primary, backgroundColor: C.primaryLight }]}>{p.jersey ?? '–'}</Text>
                          <Text style={{ color: C.text, fontSize: 13, flex: 1 }}>{p.name}</Text>
                          {p.position && (
                            <Text style={{ color: C.textMuted, fontSize: 11 }}>{p.position}</Text>
                          )}
                        </View>
                      ))}
                    </View>
                  )
                ))}
              </View>
            )}

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
  h2hRow: { height: 24, flexDirection: 'row', borderRadius: 6, overflow: 'hidden' },
  h2hBar: { height: '100%', alignItems: 'center', justifyContent: 'center' },
  h2hText: { color: '#fff', fontSize: 11, fontWeight: '800' },
  formDot: { width: 22, height: 22, borderRadius: 11, alignItems: 'center', justifyContent: 'center' },
  formDotText: { color: '#fff', fontSize: 10, fontWeight: '800' },
  infoRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, marginBottom: 10 },
  infoLabel: { fontSize: 13, fontWeight: '600' },
  infoSub: { fontSize: 11, marginTop: 1 },
  weatherBox: { borderRadius: 10, borderWidth: 1, padding: 12, marginTop: 4 },
  scorerRow: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    paddingVertical: 7, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: '#8884',
  },
  scorerMinute: { width: 34, fontSize: 12, fontWeight: '800', textAlign: 'center' },
  cardIcon: { width: 20, height: 20, borderRadius: 4, alignItems: 'center', justifyContent: 'center' },
  lineupRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 4,
  },
  lineupJersey: {
    width: 22, height: 22, borderRadius: 11, textAlign: 'center',
    fontSize: 10, fontWeight: '800', lineHeight: 22, overflow: 'hidden',
  },
  statHeaderRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    marginBottom: 8, gap: 8,
  },
  statTeamName: { flex: 1, fontSize: 12, fontWeight: '700' },
  statRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingVertical: 5, gap: 8,
  },
  statValue: { width: 64, fontSize: 13, fontWeight: '700' },
  statLabel: { flex: 1, fontSize: 11, textAlign: 'center', textTransform: 'uppercase', letterSpacing: 0.4 },
});

const probStyles = StyleSheet.create({
  track: { height: 6, borderRadius: 3, overflow: 'hidden' },
  fill: { height: '100%', borderRadius: 3 },
  oddsRow: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1,
  },
});
