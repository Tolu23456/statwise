import React from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, Platform } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { Colors } from '@/constants/colors';
import { useTheme } from '@/context/ThemeContext';

const SECTIONS = [
  {
    title: '1. Information We Collect',
    body: `We collect information you provide directly to us when you create an account, including your email address and display name.\n\nWe automatically collect usage data such as which predictions you view, your subscription tier, and app interaction patterns to improve our service.`,
  },
  {
    title: '2. How We Use Your Information',
    body: `We use collected information to:\n• Provide and improve the StatWise service\n• Generate personalized match predictions\n• Process subscription payments\n• Send notifications about new predictions (if enabled)\n• Maintain the referral program`,
  },
  {
    title: '3. Data Storage',
    body: `Your data is stored securely using Supabase (PostgreSQL) with row-level security. We do not sell your personal data to third parties. Payment information is processed securely by Flutterwave and is not stored on our servers.`,
  },
  {
    title: '4. Prediction Data',
    body: `AI-generated predictions are for informational and entertainment purposes only. StatWise does not guarantee the accuracy of predictions. Always gamble responsibly and within your means.`,
  },
  {
    title: '5. Cookies & Local Storage',
    body: `We use browser local storage to maintain your login session and theme preferences. No third-party advertising cookies are used.`,
  },
  {
    title: '6. Your Rights',
    body: `You have the right to:\n• Access your personal data\n• Request deletion of your account\n• Opt out of notifications at any time\n\nTo request account deletion, contact us at support@statwise.app`,
  },
  {
    title: '7. Changes to This Policy',
    body: `We may update this Privacy Policy from time to time. We will notify you of significant changes through the app. Continued use of the service after changes constitutes acceptance of the updated policy.`,
  },
  {
    title: '8. Contact',
    body: `For privacy-related questions, contact us at:\nsupport@statwise.app`,
  },
];

export default function PrivacyPolicyScreen() {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const topInset = Platform.OS === 'web' ? 67 : insets.top;

  return (
    <View style={[styles.container, { backgroundColor: C.background }]}>
      <View style={[styles.header, { paddingTop: topInset + 8, backgroundColor: C.background }]}>
        <TouchableOpacity onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={C.text} />
        </TouchableOpacity>
        <Text style={[styles.headerTitle, { color: C.text }]}>Privacy Policy</Text>
        <View style={{ width: 32 }} />
      </View>

      <ScrollView
        contentContainerStyle={[styles.scroll, { paddingBottom: insets.bottom + 40 }]}
        showsVerticalScrollIndicator={false}
      >
        <Text style={[styles.updated, { color: C.textMuted }]}>Last updated: April 2, 2026</Text>
        <Text style={[styles.intro, { color: C.textSecondary }]}>
          StatWise ("we", "our", or "us") is committed to protecting your privacy. This policy explains how we collect, use, and safeguard your information.
        </Text>

        {SECTIONS.map((s, i) => (
          <View key={i} style={styles.section}>
            <Text style={[styles.sectionTitle, { color: C.text }]}>{s.title}</Text>
            <Text style={[styles.sectionBody, { color: C.textSecondary }]}>{s.body}</Text>
          </View>
        ))}
      </ScrollView>
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
  scroll: { paddingHorizontal: 20, paddingTop: 4 },
  updated: { fontSize: 12, fontFamily: 'Inter_400Regular', marginBottom: 16 },
  intro: { fontSize: 15, fontFamily: 'Inter_400Regular', lineHeight: 22, marginBottom: 24 },
  section: { marginBottom: 24 },
  sectionTitle: { fontSize: 16, fontFamily: 'Inter_700Bold', marginBottom: 10 },
  sectionBody: { fontSize: 14, fontFamily: 'Inter_400Regular', lineHeight: 22 },
});
