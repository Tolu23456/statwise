import React, { useState } from 'react';
import {
  View, Text, StyleSheet, Modal, TouchableOpacity,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useTheme } from '@/context/ThemeContext';
import { Colors } from '@/constants/colors';

export const ONBOARDING_KEY = 'statwise_onboarding_v1';

const SLIDES = [
  {
    icon: 'hardware-chip-outline' as const,
    title: 'Meet Pico, your AI',
    desc: 'Pico is a gradient-boosting model trained on 300,000+ historical matches across 100+ leagues — team form, head-to-head history, Elo ratings, squad value, weather and market odds.',
    color: '#1a73e8',
  },
  {
    icon: 'football-outline' as const,
    title: 'Browse Today\'s Picks',
    desc: 'Filter by league, search for teams, and tap any card for the full breakdown: win probabilities, confidence, certainty score and AI reasoning.',
    color: '#22c55e',
  },
  {
    icon: 'diamond-outline' as const,
    title: 'Unlock More with a Plan',
    desc: 'Free users get 5 picks per day. Upgrade to Premium (25), VIP (75), or VVIP (unlimited) for more predictions, AI Picks and exclusive insights.',
    color: '#f5c518',
  },
  {
    icon: 'chatbubbles-outline' as const,
    title: 'Join the Community',
    desc: 'Discuss matches, share tips and chat with other bettors in our live community forum. Higher tiers can post and share media.',
    color: '#a855f7',
  },
  {
    icon: 'shield-checkmark-outline' as const,
    title: 'Play Responsibly',
    desc: 'No model is ever 100% accurate — Pico\'s highest-certainty picks still lose sometimes. Treat every prediction as information, not a guarantee, and only stake what you can afford to lose.',
    color: '#ef4444',
  },
];

type Props = { visible: boolean; onDone: () => void };

export function OnboardingModal({ visible, onDone }: Props) {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const [slide, setSlide] = useState(0);

  async function finish() {
    await AsyncStorage.setItem(ONBOARDING_KEY, 'true');
    onDone();
  }

  function next() {
    if (slide < SLIDES.length - 1) {
      setSlide(s => s + 1);
    } else {
      finish();
    }
  }

  const s = SLIDES[slide];

  return (
    <Modal visible={visible} animationType="fade" transparent statusBarTranslucent>
      <View style={styles.overlay}>
        <View style={[styles.modal, { backgroundColor: C.card, borderColor: C.border }]}>
          <TouchableOpacity style={styles.skip} onPress={finish} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
            <Text style={[styles.skipText, { color: C.textSecondary }]}>Skip</Text>
          </TouchableOpacity>

          <View style={[styles.iconCircle, { backgroundColor: s.color + '1A' }]}>
            <Ionicons name={s.icon} size={56} color={s.color} />
          </View>

          <Text style={[styles.slideCount, { color: C.textMuted }]}>
            {slide + 1} of {SLIDES.length}
          </Text>
          <Text style={[styles.title, { color: C.text }]}>{s.title}</Text>
          <Text style={[styles.desc, { color: C.textSecondary }]}>{s.desc}</Text>

          <View style={styles.dots}>
            {SLIDES.map((_, i) => (
              <View
                key={i}
                style={[
                  styles.dot,
                  { backgroundColor: i === slide ? C.primary : C.border, width: i === slide ? 20 : 8 },
                ]}
              />
            ))}
          </View>

          <TouchableOpacity style={[styles.btn, { backgroundColor: C.primary }]} onPress={next} activeOpacity={0.85}>
            <Text style={styles.btnText}>
              {slide === SLIDES.length - 1 ? 'Get Started' : 'Next'}
            </Text>
            <Ionicons
              name={slide === SLIDES.length - 1 ? 'checkmark' : 'arrow-forward'}
              size={18}
              color="#fff"
            />
          </TouchableOpacity>
        </View>
      </View>
    </Modal>
  );
}

export async function shouldShowOnboarding(): Promise<boolean> {
  try {
    const val = await AsyncStorage.getItem(ONBOARDING_KEY);
    return val !== 'true';
  } catch {
    return false;
  }
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.75)',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  modal: {
    borderRadius: 24,
    borderWidth: 1,
    padding: 32,
    alignItems: 'center',
    width: '100%',
    maxWidth: 420,
  },
  skip: { alignSelf: 'flex-end', marginBottom: 20 },
  skipText: { fontSize: 14, fontFamily: 'Inter_500Medium' },
  iconCircle: {
    width: 110,
    height: 110,
    borderRadius: 55,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 20,
  },
  slideCount: { fontSize: 12, fontFamily: 'Inter_500Medium', marginBottom: 10 },
  title: { fontSize: 22, fontFamily: 'Inter_700Bold', textAlign: 'center', marginBottom: 12 },
  desc: {
    fontSize: 15,
    fontFamily: 'Inter_400Regular',
    textAlign: 'center',
    lineHeight: 23,
    marginBottom: 28,
  },
  dots: { flexDirection: 'row', gap: 6, marginBottom: 28, alignItems: 'center' },
  dot: { height: 8, borderRadius: 4 },
  btn: {
    borderRadius: 14,
    paddingVertical: 14,
    paddingHorizontal: 40,
    alignItems: 'center',
    flexDirection: 'row',
    gap: 8,
  },
  btnText: { color: '#fff', fontSize: 16, fontFamily: 'Inter_600SemiBold' },
});
