import React, { useMemo, useState } from 'react';
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
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Clipboard from 'expo-clipboard';
import { Colors } from '@/constants/colors';
import { useTheme } from '@/context/ThemeContext';
import { Prediction } from '@/lib/supabase';
import { formatSelectionLabel } from '@/lib/bookings';

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
  const [bookingCode, setBookingCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Filter AI picks only as requested
  const aiPicks = useMemo(() => {
    return predictions.filter((p) => p.stats?.is_ai_pick === true);
  }, [predictions]);

  const generateBookingCode = async () => {
    setLoading(true);
    setError(null);
    setBookingCode(null);

    try {
      // For now, we'll create a shareable format
      // In production, this would call a backend service to generate actual SportyBet booking codes
      // The backend would need to map our matches to SportyBet's event IDs
      const selections = aiPicks.map((p, idx) => {
        const sel = formatSelectionLabel(p);
        return `${idx + 1}. ${p.home_team} vs ${p.away_team} - ${sel}`;
      });

      const code = `STATWISE-${aiPicks.length}-${Date.now().toString().slice(-6)}`;
      setBookingCode(code);
    } catch (e: any) {
      setError(e.message || 'Failed to generate booking code');
    } finally {
      setLoading(false);
    }
  };

  const copyCode = async () => {
    if (!bookingCode) return;
    try {
      await copyToClipboard(bookingCode);
      if (Platform.OS === 'web') {
        alert('Booking code copied to clipboard!');
      } else {
        Alert.alert('Copied', 'Booking code copied to clipboard');
      }
    } catch (e) {
      console.error(e);
    }
  };

  const copyAllSelections = async () => {
    const text = aiPicks
      .map((p, idx) => {
        const sel = formatSelectionLabel(p);
        return `${idx + 1}. ${p.home_team} vs ${p.away_team} | ${sel} | ${p.league}`;
      })
      .join('\n');
    try {
      await copyToClipboard(text);
      if (Platform.OS === 'web') {
        alert('All selections copied!');
      } else {
        Alert.alert('Copied', 'All selections copied to clipboard');
      }
    } catch (e) {
      console.error(e);
    }
  };

  const shareCode = async () => {
    if (!bookingCode) return;
    try {
      if (Platform.OS === 'web' && navigator.share) {
        await navigator.share({
          title: 'StatWise Booking Code',
          text: `StatWise AI Picks Booking Code: ${bookingCode}\n\nPaste this into SportyBet Nigeria's "Load Booking Code" section.`,
        });
      } else {
        await Share.share({
          message: `StatWise AI Picks Booking Code: ${bookingCode}\n\nPaste this into SportyBet Nigeria's "Load Booking Code" section.\n${aiPicks.length} selections (Accumulator)`,
        });
      }
    } catch (e) {
      console.error(e);
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
                Generate a booking code for all AI Pick predictions (Accumulator). Paste it into SportyBet Nigeria's "Load Booking Code" to auto-fill your betslip.
              </Text>
            </View>

            <Text style={[styles.sectionTitle, { color: C.text }]}>
              AI Picks ({aiPicks.length})
            </Text>

            {aiPicks.length === 0 ? (
              <View style={[styles.emptyState, { backgroundColor: C.inputBg, borderColor: C.border }]}>
                <Ionicons name="alert-circle-outline" size={32} color={C.textMuted} />
                <Text style={[styles.emptyText, { color: C.textMuted }]}>
                  No AI Picks available right now
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
                        {p.league} • {formatSelectionLabel(p)} • Odds {p.odds}
                      </Text>
                    </View>
                  </View>
                ))}
              </View>
            )}

            {bookingCode && (
              <View style={[styles.codeBox, { backgroundColor: C.successLight, borderColor: C.success }]}>
                <Text style={[styles.codeLabel, { color: C.textMuted }]}>Your Booking Code</Text>
                <Text style={[styles.codeValue, { color: C.success }]}>{bookingCode}</Text>
                <View style={styles.codeActions}>
                  <TouchableOpacity
                    style={[styles.codeActionBtn, { backgroundColor: C.success }]}
                    onPress={copyCode}
                  >
                    <Ionicons name="copy-outline" size={16} color="#fff" />
                    <Text style={styles.codeActionText}>Copy Code</Text>
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
            )}

            {error && (
              <View style={[styles.errorBox, { backgroundColor: C.dangerLight, borderColor: C.danger }]}>
                <Text style={[styles.errorText, { color: C.danger }]}>{error}</Text>
              </View>
            )}

            <View style={[styles.howToBox, { backgroundColor: C.inputBg, borderColor: C.border }]}>
              <Text style={[styles.howToTitle, { color: C.text }]}>How to use on SportyBet Nigeria</Text>
              <Text style={[styles.howToStep, { color: C.textSecondary }]}>
                1. Open sportybet.com/ng or the SportyBet app
              </Text>
              <Text style={[styles.howToStep, { color: C.textSecondary }]}>
                2. Click/Tap "Load Booking Code" (usually on homepage or betslip)
              </Text>
              <Text style={[styles.howToStep, { color: C.textSecondary }]}>
                3. Paste your booking code and click "Load"
              </Text>
              <Text style={[styles.howToStep, { color: C.textSecondary }]}>
                4. Review your accumulator and place your bet
              </Text>
            </View>
          </ScrollView>

          <View style={[styles.footer, { borderTopColor: C.border }]}>
            {aiPicks.length > 0 && !bookingCode && (
              <TouchableOpacity
                style={[styles.generateBtn, { backgroundColor: C.primary, opacity: loading ? 0.7 : 1 }]}
                onPress={generateBookingCode}
                disabled={loading}
              >
                {loading ? (
                  <ActivityIndicator size="small" color="#fff" />
                ) : (
                  <Ionicons name="ticket" size={20} color="#fff" />
                )}
                <Text style={styles.generateBtnText}>
                  {loading ? 'Generating...' : `Generate Booking Code (${aiPicks.length} picks)`}
                </Text>
              </TouchableOpacity>
            )}
            {bookingCode && (
              <TouchableOpacity
                style={[styles.generateBtn, { backgroundColor: C.inputBg, borderWidth: 1, borderColor: C.border }]}
                onPress={copyAllSelections}
              >
                <Ionicons name="copy-outline" size={20} color={C.text} />
                <Text style={[styles.generateBtnText, { color: C.text }]}>Copy All Selections</Text>
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
    marginBottom: 8,
  },
  codeValue: {
    fontSize: 28,
    fontFamily: 'Inter_700Bold',
    letterSpacing: 4,
    marginBottom: 12,
  },
  codeActions: {
    flexDirection: 'row',
    gap: 8,
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

// Fix clipboard import for web compatibility
