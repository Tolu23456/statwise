import React, { useEffect, useMemo, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Modal,
  TouchableOpacity,
  ScrollView,
  Platform,
  Share,
  Alert,
  ActivityIndicator,
  Linking,
} from 'react-native';
import Ionicons from '@expo/vector-icons/Ionicons';
import * as Clipboard from 'expo-clipboard';
import { Colors } from '@/constants/colors';
import { useTheme } from '@/context/ThemeContext';
import { Prediction } from '@/lib/supabase';
import {
  BookingCode,
  BookingStats,
  fetchBookingStats,
  fetchLatestBookingCode,
  formatSelectionLabel,
  safeShareUrl,
} from '@/lib/bookings';

const copyToClipboard = async (text: string) => {
  try {
    await Clipboard.setStringAsync(text);
    return true;
  } catch {
    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return true;
    }
    throw new Error('Failed to copy');
  }
};

type Props = {
  visible: boolean;
  onClose: () => void;
  predictions: Prediction[];
};

export function BookingCodeModal({ visible, onClose, predictions }: Props) {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const [loading, setLoading] = useState(false);
  const [bookingCode, setBookingCode] = useState<BookingCode | null>(null);
  const [genStats, setGenStats] = useState<BookingStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The booking code is built by the backend from AI Picks only.
  const aiPicks = useMemo(
    () => predictions.filter((p) => p.stats?.is_ai_pick === true),
    [predictions],
  );

  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.allSettled([fetchLatestBookingCode(), fetchBookingStats()])
      .then(([codeRes, statsRes]) => {
        if (cancelled) return;
        if (codeRes.status === 'fulfilled') setBookingCode(codeRes.value);
        else setError(codeRes.reason?.message || 'Could not load the booking code');
        if (statsRes.status === 'fulfilled') setGenStats(statsRes.value);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [visible]);

  const slipText = () =>
    aiPicks
      .map((p, idx) => {
        const sel = formatSelectionLabel(p);
        const odds = p.odds != null ? ` @ ${p.odds}` : '';
        return `${idx + 1}. ${p.home_team} vs ${p.away_team} | ${sel}${odds} | ${p.league}`;
      })
      .join('\n');

  const copyCode = async () => {
    if (!bookingCode) return;
    try {
      await copyToClipboard(bookingCode.code);
      if (Platform.OS === 'web') alert('Booking code copied!');
      else Alert.alert('Copied', 'Booking code copied to clipboard');
    } catch (e) {
      setError('Could not copy — select the code and copy it manually.');
    }
  };

  const copyAllSelections = async () => {
    try {
      await copyToClipboard(slipText());
      if (Platform.OS === 'web') alert('All selections copied!');
      else Alert.alert('Copied', 'All selections copied to clipboard');
    } catch (e) {
      setError('Could not copy the selections.');
    }
  };

  const openSportyBet = async () => {
    // safeShareUrl: https + sportybet.com only (falls back to a URL we build
    // from the validated code) — a tampered settings row can't redirect this.
    const url = bookingCode
      ? safeShareUrl(bookingCode)
      : 'https://www.sportybet.com/ng/';
    if (!url) return;
    try {
      if (Platform.OS === 'web') {
        window.open(url, '_blank');
      } else {
        await Linking.openURL(url);
      }
    } catch {
      setError('Could not open SportyBet.');
    }
  };

  const shareCode = async () => {
    if (!bookingCode) return;
    const url = safeShareUrl(bookingCode);
    if (!url) return;
    const message =
      `SportyBet booking code: ${bookingCode.code}\n` +
      `${aiPicks.length} AI Picks (accumulator)\n` +
      `Load it: ${url}`;
    try {
      if (Platform.OS === 'web' && navigator.share) {
        await navigator.share({ title: 'StatWise Booking Code', text: message });
      } else {
        await Share.share({ message });
      }
    } catch {
      /* user dismissed */
    }
  };

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.overlay}>
        <View style={[styles.container, { backgroundColor: C.card, borderColor: C.border }]}>
          <View style={styles.header}>
            <View style={styles.headerLeft}>
              <Ionicons name="ticket-outline" size={24} color={C.primary} />
              <Text style={[styles.title, { color: C.text }]}>SportyBet Booking Code</Text>
            </View>
            <TouchableOpacity onPress={onClose} hitSlop={10}>
              <Ionicons name="close" size={24} color={C.textMuted} />
            </TouchableOpacity>
          </View>

          <ScrollView style={styles.content} showsVerticalScrollIndicator={false}>
            <View style={[styles.infoBox, { backgroundColor: C.primaryLight, borderColor: C.primary }]}>
              <Ionicons name="information-circle" size={20} color={C.primary} />
              <Text style={[styles.infoText, { color: C.text }]}>
                One accumulator built only from AI Picks. The code is generated on the
                server and refreshed whenever Pico publishes new picks.
              </Text>
            </View>

            {loading ? (
              <View style={styles.emptyState}>
                <ActivityIndicator size="small" color={C.primary} />
                <Text style={[styles.emptyText, { color: C.textMuted }]}>
                  Loading the latest booking code…
                </Text>
              </View>
            ) : bookingCode ? (
              <View style={[styles.codeBox, { backgroundColor: C.successLight, borderColor: C.success }]}>
                <Text style={[styles.codeLabel, { color: C.textMuted }]}>BOOKING CODE</Text>
                <Text style={[styles.codeValue, { color: C.success }]} selectable>
                  {bookingCode.code}
                </Text>
                <Text style={[styles.codeMeta, { color: C.textMuted }]}>
                  {bookingCode.selections.length || aiPicks.length} selections · accumulator
                  {bookingCode.createdAt
                    ? ` · updated ${new Date(bookingCode.createdAt).toLocaleString()}`
                    : ''}
                </Text>
                <View style={styles.codeActions}>
                  <TouchableOpacity
                    style={[styles.codeActionBtn, { backgroundColor: C.success }]}
                    onPress={openSportyBet}
                  >
                    <Ionicons name="open-outline" size={16} color="#fff" />
                    <Text style={styles.codeActionText}>Load on SportyBet</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={[styles.codeActionBtn, { backgroundColor: C.success }]}
                    onPress={copyCode}
                  >
                    <Ionicons name="copy-outline" size={16} color="#fff" />
                    <Text style={styles.codeActionText}>Copy</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={[styles.codeActionBtn, { backgroundColor: C.success }]}
                    onPress={shareCode}
                  >
                    <Ionicons name="share-outline" size={16} color="#fff" />
                    <Text style={styles.codeActionText}>Share</Text>
                  </TouchableOpacity>
                </View>
              </View>
            ) : (
              <View style={[styles.emptyState, { backgroundColor: C.inputBg, borderColor: C.border }]}>
                <Ionicons name="time-outline" size={32} color={C.textMuted} />
                <Text style={[styles.emptyText, { color: C.textMuted, textAlign: 'center' }]}>
                  No booking code has been generated for the current picks yet.
                </Text>
                {genStats?.lastError && (
                  <View style={[styles.errorBox, { backgroundColor: C.dangerLight, borderColor: C.danger, marginTop: 10 }]}>
                    <Text style={[styles.errorText, { color: C.danger }]}>
                      Last attempt failed{genStats.lastErrorAt
                        ? ` (${new Date(genStats.lastErrorAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })})`
                        : ''}: {genStats.lastError}
                    </Text>
                  </View>
                )}
                <Text style={[styles.emptyText, { color: C.textMuted, textAlign: 'center', fontSize: 12 }]}>
                  Codes are created automatically after each prediction run. You can copy the
                  selections below and place them manually in the meantime.
                </Text>
              </View>
            )}

            <Text style={[styles.sectionTitle, { color: C.text, marginTop: 20 }]}>
              AI Picks ({aiPicks.length})
            </Text>

            {aiPicks.length === 0 ? (
              <View style={[styles.emptyState, { backgroundColor: C.inputBg, borderColor: C.border }]}>
                <Ionicons name="alert-circle-outline" size={32} color={C.textMuted} />
                <Text style={[styles.emptyText, { color: C.textMuted, textAlign: 'center' }]}>
                  No AI Picks right now. Pico only promotes a fixture when certainty is high
                  enough, so a short list (or an empty one) is normal.
                </Text>
              </View>
            ) : (
              <View style={styles.selectionsList}>
                {aiPicks.map((p, idx) => (
                  <View
                    key={p.id}
                    style={[styles.selectionItem, { backgroundColor: C.inputBg, borderColor: C.border }]}
                  >
                    <Text style={[styles.selectionNumber, { color: C.primary }]}>{idx + 1}</Text>
                    <View style={styles.selectionInfo}>
                      <Text style={[styles.selectionMatch, { color: C.text }]} numberOfLines={1}>
                        {p.home_team} vs {p.away_team}
                      </Text>
                      <Text style={[styles.selectionLeague, { color: C.textMuted }]} numberOfLines={1}>
                        {p.league} • {formatSelectionLabel(p)}
                        {p.odds != null ? ` • ${p.odds}` : ''}
                      </Text>
                    </View>
                  </View>
                ))}
              </View>
            )}

            {error && (
              <View style={[styles.errorBox, { backgroundColor: C.dangerLight, borderColor: C.danger }]}>
                <Text style={[styles.errorText, { color: C.danger }]}>{error}</Text>
              </View>
            )}

            <View style={[styles.howToBox, { backgroundColor: C.inputBg, borderColor: C.border }]}>
              <Text style={[styles.howToTitle, { color: C.text }]}>How to use on SportyBet Nigeria</Text>
              <Text style={[styles.howToStep, { color: C.textSecondary }]}>
                1. Tap “Load on SportyBet” above (or open sportybet.com/ng)
              </Text>
              <Text style={[styles.howToStep, { color: C.textSecondary }]}>
                2. Open “Load Booking Code” and paste the code
              </Text>
              <Text style={[styles.howToStep, { color: C.textSecondary }]}>
                3. Review the accumulator and place your bet
              </Text>
            </View>
          </ScrollView>

          <View style={[styles.footer, { borderTopColor: C.border }]}>
            <TouchableOpacity
              style={[styles.generateBtn, { backgroundColor: C.primary }]}
              onPress={openSportyBet}
            >
              <Ionicons name="open-outline" size={20} color="#fff" />
              <Text style={styles.generateBtnText}>Open SportyBet</Text>
            </TouchableOpacity>
            {aiPicks.length > 0 && (
              <TouchableOpacity
                style={[
                  styles.generateBtn,
                  {
                    backgroundColor: C.inputBg,
                    borderWidth: 1,
                    borderColor: C.border,
                    marginTop: 8,
                  },
                ]}
                onPress={copyAllSelections}
              >
                <Ionicons name="copy-outline" size={20} color={C.text} />
                <Text style={[styles.generateBtnText, { color: C.text }]}>
                  Copy All Selections
                </Text>
              </TouchableOpacity>
            )}
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.7)',
    justifyContent: 'flex-end',
  },
  container: {
    height: '90%',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    borderWidth: 1,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 20,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.1)',
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  title: {
    fontSize: 18,
    fontFamily: 'Inter_700Bold',
  },
  content: {
    flex: 1,
    padding: 20,
  },
  infoBox: {
    flexDirection: 'row',
    gap: 10,
    padding: 12,
    borderRadius: 12,
    borderWidth: 1,
    marginBottom: 20,
  },
  infoText: {
    flex: 1,
    fontSize: 13,
    fontFamily: 'Inter_400Regular',
    lineHeight: 18,
  },
  sectionTitle: {
    fontSize: 16,
    fontFamily: 'Inter_600SemiBold',
    marginBottom: 12,
  },
  emptyState: {
    alignItems: 'center',
    padding: 32,
    borderRadius: 12,
    borderWidth: 1,
    gap: 8,
  },
  emptyText: {
    fontSize: 14,
    fontFamily: 'Inter_400Regular',
  },
  selectionsList: {
    gap: 8,
    marginBottom: 20,
  },
  selectionItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 12,
    borderRadius: 12,
    borderWidth: 1,
  },
  selectionNumber: {
    fontSize: 14,
    fontFamily: 'Inter_700Bold',
    width: 24,
  },
  selectionInfo: {
    flex: 1,
  },
  selectionMatch: {
    fontSize: 14,
    fontFamily: 'Inter_600SemiBold',
  },
  selectionLeague: {
    fontSize: 12,
    fontFamily: 'Inter_400Regular',
    marginTop: 2,
  },
  codeBox: {
    padding: 16,
    borderRadius: 12,
    borderWidth: 1,
    alignItems: 'center',
    marginBottom: 20,
  },
  codeLabel: {
    fontSize: 12,
    fontFamily: 'Inter_400Regular',
    letterSpacing: 1,
    marginBottom: 8,
  },
  codeValue: {
    fontSize: 28,
    fontFamily: 'Inter_700Bold',
    letterSpacing: 4,
    marginBottom: 8,
  },
  codeMeta: {
    fontSize: 11,
    fontFamily: 'Inter_400Regular',
    marginBottom: 12,
    textAlign: 'center',
  },
  codeActions: {
    flexDirection: 'row',
    gap: 8,
    flexWrap: 'wrap',
    justifyContent: 'center',
  },
  codeActionBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 8,
  },
  codeActionText: {
    color: '#fff',
    fontSize: 13,
    fontFamily: 'Inter_600SemiBold',
  },
  errorBox: {
    padding: 12,
    borderRadius: 12,
    borderWidth: 1,
    marginBottom: 20,
  },
  errorText: {
    fontSize: 13,
    fontFamily: 'Inter_400Regular',
  },
  howToBox: {
    padding: 16,
    borderRadius: 12,
    borderWidth: 1,
    marginBottom: 20,
  },
  howToTitle: {
    fontSize: 14,
    fontFamily: 'Inter_600SemiBold',
    marginBottom: 10,
  },
  howToStep: {
    fontSize: 13,
    fontFamily: 'Inter_400Regular',
    marginBottom: 6,
    lineHeight: 18,
  },
  footer: {
    padding: 20,
    borderTopWidth: 1,
  },
  generateBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingVertical: 14,
    borderRadius: 12,
  },
  generateBtnText: {
    color: '#fff',
    fontSize: 15,
    fontFamily: 'Inter_600SemiBold',
  },
});
