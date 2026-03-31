import React, { useState } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet,
  useColorScheme, ActivityIndicator,
} from 'react-native';
import { Link, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { Colors } from '@/constants/colors';
import { supabase } from '@/lib/supabase';

export default function EmailConfirmationScreen() {
  const scheme = useColorScheme() ?? 'dark';
  const C = Colors[scheme];
  const insets = useSafeAreaInsets();
  const { email } = useLocalSearchParams<{ email: string }>();

  const [resending, setResending] = useState(false);
  const [resendStatus, setResendStatus] = useState<'idle' | 'sent' | 'error'>('idle');
  const [resendMsg, setResendMsg] = useState('');

  async function handleResend() {
    if (!email) return;
    setResending(true);
    setResendStatus('idle');
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    const { error } = await supabase.auth.resend({ type: 'signup', email });
    setResending(false);
    if (error) {
      const msg = error.message.toLowerCase();
      if (msg.includes('rate') || msg.includes('too many')) {
        setResendMsg('Please wait a few minutes before requesting another email.');
      } else {
        setResendMsg(error.message);
      }
      setResendStatus('error');
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    } else {
      setResendMsg('A new verification email has been sent.');
      setResendStatus('sent');
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    }
  }

  return (
    <View
      style={[
        styles.container,
        {
          backgroundColor: C.background,
          paddingTop: insets.top + 20,
          paddingBottom: insets.bottom + 24,
        },
      ]}
    >
      <View style={styles.inner}>
        <View style={[styles.iconCircle, { backgroundColor: C.primaryLight }]}>
          <Ionicons name="mail-outline" size={44} color={C.primary} />
        </View>

        <Text style={[styles.title, { color: C.text }]}>Check your email</Text>

        <Text style={[styles.body, { color: C.textSecondary }]}>
          We sent a verification link to
        </Text>
        <Text style={[styles.email, { color: C.primary }]}>{email ?? 'your email address'}</Text>

        <Text style={[styles.hint, { color: C.textSecondary }]}>
          Click the link in the email to activate your account. It may take a minute to arrive —
          check your spam or junk folder if you don't see it.
        </Text>

        {resendStatus === 'sent' && (
          <View style={[styles.statusBanner, { backgroundColor: C.successLight, borderColor: C.success }]}>
            <Ionicons name="checkmark-circle-outline" size={16} color={C.success} />
            <Text style={[styles.statusText, { color: C.success }]}>{resendMsg}</Text>
          </View>
        )}
        {resendStatus === 'error' && (
          <View style={[styles.statusBanner, { backgroundColor: C.dangerLight, borderColor: C.danger }]}>
            <Ionicons name="alert-circle-outline" size={16} color={C.danger} />
            <Text style={[styles.statusText, { color: C.danger }]}>{resendMsg}</Text>
          </View>
        )}

        <TouchableOpacity
          style={[
            styles.resendBtn,
            {
              borderColor: C.border,
              backgroundColor: C.card,
              opacity: resending ? 0.6 : 1,
            },
          ]}
          onPress={handleResend}
          disabled={resending}
        >
          {resending ? (
            <ActivityIndicator size="small" color={C.primary} />
          ) : (
            <>
              <Ionicons name="refresh-outline" size={18} color={C.primary} />
              <Text style={[styles.resendText, { color: C.primary }]}>Resend verification email</Text>
            </>
          )}
        </TouchableOpacity>

        <Link href="/(auth)/login" asChild replace>
          <TouchableOpacity style={[styles.loginBtn, { backgroundColor: C.primary }]}>
            <Text style={styles.loginBtnText}>Back to Sign In</Text>
          </TouchableOpacity>
        </Link>

        <View style={styles.stepRow}>
          <View style={[styles.step, { backgroundColor: C.primary }]} />
          <View style={[styles.step, { backgroundColor: C.primary }]} />
          <View style={[styles.step, { backgroundColor: C.border }]} />
        </View>
        <Text style={[styles.stepLabel, { color: C.textMuted }]}>Step 2 of 3 — Verify email</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 28 },
  inner: { width: '100%', maxWidth: 400, alignItems: 'center' },
  iconCircle: {
    width: 96, height: 96, borderRadius: 48,
    alignItems: 'center', justifyContent: 'center',
    marginBottom: 28,
  },
  title: { fontSize: 26, fontFamily: 'Inter_700Bold', marginBottom: 10, textAlign: 'center' },
  body: { fontSize: 15, fontFamily: 'Inter_400Regular', textAlign: 'center' },
  email: {
    fontSize: 16, fontFamily: 'Inter_600SemiBold',
    textAlign: 'center', marginTop: 4, marginBottom: 20,
  },
  hint: {
    fontSize: 13, fontFamily: 'Inter_400Regular',
    textAlign: 'center', lineHeight: 20, marginBottom: 28,
  },
  statusBanner: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 8,
    borderRadius: 10, borderWidth: 1, padding: 12, marginBottom: 16,
    width: '100%',
  },
  statusText: { flex: 1, fontSize: 13, fontFamily: 'Inter_400Regular', lineHeight: 18 },
  resendBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    borderRadius: 14, borderWidth: 1, paddingVertical: 14,
    paddingHorizontal: 20, width: '100%', justifyContent: 'center',
    marginBottom: 12,
  },
  resendText: { fontSize: 15, fontFamily: 'Inter_600SemiBold' },
  loginBtn: {
    borderRadius: 14, paddingVertical: 16,
    width: '100%', alignItems: 'center', marginBottom: 32,
  },
  loginBtnText: { color: '#fff', fontSize: 16, fontFamily: 'Inter_600SemiBold' },
  stepRow: { flexDirection: 'row', gap: 6, marginBottom: 8 },
  step: { width: 28, height: 4, borderRadius: 2 },
  stepLabel: { fontSize: 12, fontFamily: 'Inter_400Regular' },
});
