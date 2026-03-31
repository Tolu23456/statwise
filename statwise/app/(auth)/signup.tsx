import React, { useState, useRef } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  KeyboardAvoidingView, Platform, ScrollView, useColorScheme,
  ActivityIndicator, TextInput as TextInputType,
} from 'react-native';
import { Link, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { Colors } from '@/constants/colors';
import { supabase } from '@/lib/supabase';

type FieldErrors = {
  displayName?: string;
  email?: string;
  password?: string;
  confirmPassword?: string;
  general?: string;
};

function parseSignupError(message: string): FieldErrors {
  const msg = message.toLowerCase();
  if (
    msg.includes('user already registered') ||
    msg.includes('already registered') ||
    msg.includes('email already') ||
    msg.includes('already in use')
  ) {
    return { email: 'An account with this email already exists. Try signing in instead.' };
  }
  if (msg.includes('invalid email') || msg.includes('email is invalid')) {
    return { email: "Enter a valid email address (e.g. you@example.com)." };
  }
  if (
    msg.includes('password') &&
    (msg.includes('short') || msg.includes('weak') || msg.includes('characters') || msg.includes('length'))
  ) {
    return { password: 'Password is too weak. Use at least 6 characters.' };
  }
  if (msg.includes('too many') || msg.includes('rate limit')) {
    return { general: 'Too many attempts. Please wait a minute and try again.' };
  }
  if (msg.includes('network') || msg.includes('fetch') || msg.includes('connection')) {
    return { general: 'Connection failed. Check your internet and try again.' };
  }
  return { general: message };
}

function isValidEmail(email: string) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
}

function getPasswordStrength(pw: string): { label: string; color: string; pct: number } {
  if (!pw) return { label: '', color: 'transparent', pct: 0 };
  if (pw.length < 6) return { label: 'Too short', color: '#ef4444', pct: 15 };
  if (pw.length < 8) return { label: 'Weak', color: '#f59e0b', pct: 35 };
  const score = [/[A-Z]/.test(pw), /[0-9]/.test(pw), /[^A-Za-z0-9]/.test(pw)].filter(Boolean).length;
  if (score === 0) return { label: 'Fair', color: '#f59e0b', pct: 55 };
  if (score === 1) return { label: 'Good', color: '#22c55e', pct: 75 };
  return { label: 'Strong', color: '#16a34a', pct: 100 };
}

export default function SignupScreen() {
  const scheme = useColorScheme() ?? 'dark';
  const C = Colors[scheme];
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const emailRef = useRef<TextInputType>(null);
  const passwordRef = useRef<TextInputType>(null);
  const confirmRef = useRef<TextInputType>(null);

  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});

  function clearFieldError(field: keyof FieldErrors) {
    if (errors[field]) setErrors(prev => ({ ...prev, [field]: undefined }));
  }

  function validateLocally(): boolean {
    const newErrors: FieldErrors = {};
    if (!displayName.trim()) {
      newErrors.displayName = 'Display name is required.';
    } else if (displayName.trim().length < 2) {
      newErrors.displayName = 'Name must be at least 2 characters.';
    }
    if (!email.trim()) {
      newErrors.email = 'Email is required.';
    } else if (!isValidEmail(email)) {
      newErrors.email = 'Enter a valid email address (e.g. you@example.com).';
    }
    if (!password) {
      newErrors.password = 'Password is required.';
    } else if (password.length < 6) {
      newErrors.password = 'Password must be at least 6 characters.';
    }
    if (!confirmPassword) {
      newErrors.confirmPassword = 'Please confirm your password.';
    } else if (password !== confirmPassword) {
      newErrors.confirmPassword = "Passwords don't match. Try again.";
    }
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }

  async function handleSignup() {
    setErrors({});
    if (!validateLocally()) {
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
      return;
    }
    setLoading(true);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    const { error } = await supabase.auth.signUp({
      email: email.trim(),
      password,
      options: { data: { display_name: displayName.trim() } },
    });
    setLoading(false);
    if (error) {
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      setErrors(parseSignupError(error.message));
    } else {
      router.replace({
        pathname: '/(auth)/email-confirmation',
        params: { email: email.trim() },
      });
    }
  }

  const strength = getPasswordStrength(password);
  const passwordsMatch = confirmPassword.length > 0 && password === confirmPassword && !errors.confirmPassword;

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: C.background }}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
    >
      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          { paddingTop: insets.top + 32, paddingBottom: insets.bottom + 40 },
        ]}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.inner}>
          <View style={styles.header}>
            <Text style={[styles.logo, { color: C.primary }]}>StatWise</Text>
            <Text style={[styles.tagline, { color: C.textSecondary }]}>
              Create your free account
            </Text>
          </View>

          <View style={[styles.card, { backgroundColor: C.card, borderColor: C.border }]}>
            <Text style={[styles.title, { color: C.text }]}>Create Account</Text>
            <Text style={[styles.subtitle, { color: C.textSecondary }]}>
              Join thousands of sports bettors
            </Text>

            {errors.general && (
              <View style={[styles.generalError, { backgroundColor: C.dangerLight, borderColor: C.danger }]}>
                <Ionicons name="alert-circle-outline" size={16} color={C.danger} />
                <Text style={[styles.generalErrorText, { color: C.danger }]}>{errors.general}</Text>
              </View>
            )}

            <Text style={[styles.label, { color: C.textSecondary }]}>Display Name</Text>
            <TextInput
              style={[
                styles.input,
                {
                  backgroundColor: errors.displayName ? C.dangerLight : C.inputBg,
                  borderColor: errors.displayName ? C.danger : C.border,
                  color: C.text,
                },
              ]}
              placeholder="Your name"
              placeholderTextColor={C.placeholder}
              value={displayName}
              onChangeText={v => { setDisplayName(v); clearFieldError('displayName'); }}
              autoCapitalize="words"
              returnKeyType="next"
              onSubmitEditing={() => emailRef.current?.focus()}
            />
            {errors.displayName && <FieldErrorMsg text={errors.displayName} color={C.danger} />}

            <Text style={[styles.label, { color: C.textSecondary }]}>Email</Text>
            <TextInput
              ref={emailRef}
              style={[
                styles.input,
                {
                  backgroundColor: errors.email ? C.dangerLight : C.inputBg,
                  borderColor: errors.email ? C.danger : C.border,
                  color: C.text,
                },
              ]}
              placeholder="you@example.com"
              placeholderTextColor={C.placeholder}
              value={email}
              onChangeText={v => { setEmail(v); clearFieldError('email'); clearFieldError('general'); }}
              autoCapitalize="none"
              keyboardType="email-address"
              returnKeyType="next"
              onSubmitEditing={() => passwordRef.current?.focus()}
            />
            {errors.email && (
              <View>
                <FieldErrorMsg text={errors.email} color={C.danger} />
                {errors.email.includes('already exists') && (
                  <Link href="/(auth)/login" asChild>
                    <TouchableOpacity style={{ marginTop: -8, marginBottom: 8 }}>
                      <Text style={[styles.signInLink, { color: C.primary }]}>Sign in instead →</Text>
                    </TouchableOpacity>
                  </Link>
                )}
              </View>
            )}

            <Text style={[styles.label, { color: C.textSecondary }]}>Password</Text>
            <View style={styles.passwordWrapper}>
              <TextInput
                ref={passwordRef}
                style={[
                  styles.passwordInput,
                  {
                    backgroundColor: errors.password ? C.dangerLight : C.inputBg,
                    borderColor: errors.password ? C.danger : C.border,
                    color: C.text,
                  },
                ]}
                placeholder="Min 6 characters"
                placeholderTextColor={C.placeholder}
                value={password}
                onChangeText={v => { setPassword(v); clearFieldError('password'); }}
                secureTextEntry={!showPassword}
                returnKeyType="next"
                onSubmitEditing={() => confirmRef.current?.focus()}
              />
              <TouchableOpacity
                style={styles.eyeBtn}
                onPress={() => setShowPassword(p => !p)}
                hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
              >
                <Ionicons
                  name={showPassword ? 'eye-off-outline' : 'eye-outline'}
                  size={20}
                  color={C.textSecondary}
                />
              </TouchableOpacity>
            </View>
            {errors.password && <FieldErrorMsg text={errors.password} color={C.danger} />}
            {password.length > 0 && !errors.password && (
              <View style={styles.strengthRow}>
                <View style={[styles.strengthTrack, { backgroundColor: C.border }]}>
                  <View
                    style={[
                      styles.strengthFill,
                      { width: `${strength.pct}%` as any, backgroundColor: strength.color },
                    ]}
                  />
                </View>
                <Text style={[styles.strengthLabel, { color: strength.color }]}>{strength.label}</Text>
              </View>
            )}

            <Text style={[styles.label, { color: C.textSecondary }]}>Confirm Password</Text>
            <View style={styles.passwordWrapper}>
              <TextInput
                ref={confirmRef}
                style={[
                  styles.passwordInput,
                  {
                    backgroundColor: errors.confirmPassword ? C.dangerLight : C.inputBg,
                    borderColor: errors.confirmPassword
                      ? C.danger
                      : passwordsMatch
                      ? C.success
                      : C.border,
                    color: C.text,
                  },
                ]}
                placeholder="Repeat your password"
                placeholderTextColor={C.placeholder}
                value={confirmPassword}
                onChangeText={v => { setConfirmPassword(v); clearFieldError('confirmPassword'); }}
                secureTextEntry={!showConfirm}
                returnKeyType="done"
                onSubmitEditing={handleSignup}
              />
              <TouchableOpacity
                style={styles.eyeBtn}
                onPress={() => setShowConfirm(p => !p)}
                hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
              >
                <Ionicons
                  name={showConfirm ? 'eye-off-outline' : 'eye-outline'}
                  size={20}
                  color={C.textSecondary}
                />
              </TouchableOpacity>
            </View>
            {errors.confirmPassword && <FieldErrorMsg text={errors.confirmPassword} color={C.danger} />}
            {passwordsMatch && (
              <View style={styles.matchRow}>
                <Ionicons name="checkmark-circle" size={13} color={C.success} />
                <Text style={[styles.matchText, { color: C.success }]}>Passwords match</Text>
              </View>
            )}

            <TouchableOpacity
              style={[styles.button, { backgroundColor: C.primary, opacity: loading ? 0.7 : 1 }]}
              onPress={handleSignup}
              disabled={loading}
            >
              {loading ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={styles.buttonText}>Create Account</Text>
              )}
            </TouchableOpacity>
          </View>

          <View style={styles.footer}>
            <Text style={[styles.footerText, { color: C.textSecondary }]}>
              Already have an account?{' '}
            </Text>
            <Link href="/(auth)/login" asChild>
              <TouchableOpacity>
                <Text style={[styles.footerLink, { color: C.primary }]}>Sign In</Text>
              </TouchableOpacity>
            </Link>
          </View>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function FieldErrorMsg({ text, color }: { text: string; color: string }) {
  return (
    <View style={fieldStyles.row}>
      <Ionicons name="close-circle" size={13} color={color} />
      <Text style={[fieldStyles.text, { color }]}>{text}</Text>
    </View>
  );
}

const fieldStyles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'flex-start', gap: 5, marginBottom: 12, marginTop: 2 },
  text: { fontSize: 12, fontFamily: 'Inter_400Regular', flex: 1, lineHeight: 17 },
});

const styles = StyleSheet.create({
  scroll: {
    flexGrow: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 20,
  },
  inner: { width: '100%', maxWidth: 440 },
  header: { alignItems: 'center', marginBottom: 28 },
  logo: { fontSize: 32, fontFamily: 'Inter_700Bold', letterSpacing: -1 },
  tagline: { fontSize: 14, marginTop: 6, fontFamily: 'Inter_400Regular' },
  card: { borderRadius: 20, borderWidth: 1, padding: 24, marginBottom: 24 },
  title: { fontSize: 22, fontFamily: 'Inter_700Bold', marginBottom: 6 },
  subtitle: { fontSize: 14, fontFamily: 'Inter_400Regular', marginBottom: 20 },
  generalError: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 8,
    borderRadius: 10, borderWidth: 1, padding: 12, marginBottom: 16,
  },
  generalErrorText: { flex: 1, fontSize: 14, fontFamily: 'Inter_400Regular', lineHeight: 19 },
  label: { fontSize: 13, fontFamily: 'Inter_500Medium', marginBottom: 6, marginTop: 4 },
  input: {
    borderRadius: 12, borderWidth: 1, padding: 14, fontSize: 15,
    fontFamily: 'Inter_400Regular', marginBottom: 4,
  },
  passwordWrapper: { position: 'relative', marginBottom: 4 },
  passwordInput: {
    borderRadius: 12, borderWidth: 1, paddingLeft: 14, paddingRight: 46,
    paddingVertical: 14, fontSize: 15, fontFamily: 'Inter_400Regular',
  },
  eyeBtn: { position: 'absolute', right: 14, top: 0, bottom: 0, justifyContent: 'center' },
  strengthRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 12, marginTop: 4 },
  strengthTrack: { flex: 1, height: 4, borderRadius: 2, overflow: 'hidden' },
  strengthFill: { height: '100%', borderRadius: 2 },
  strengthLabel: { fontSize: 12, fontFamily: 'Inter_600SemiBold', width: 52, textAlign: 'right' },
  matchRow: { flexDirection: 'row', alignItems: 'center', gap: 5, marginBottom: 12, marginTop: 2 },
  matchText: { fontSize: 12, fontFamily: 'Inter_400Regular' },
  signInLink: { fontSize: 12, fontFamily: 'Inter_600SemiBold' },
  button: { borderRadius: 14, padding: 16, alignItems: 'center', marginTop: 8 },
  buttonText: { color: '#fff', fontSize: 16, fontFamily: 'Inter_600SemiBold' },
  footer: { flexDirection: 'row', justifyContent: 'center', alignItems: 'center' },
  footerText: { fontSize: 15, fontFamily: 'Inter_400Regular' },
  footerLink: { fontSize: 15, fontFamily: 'Inter_600SemiBold' },
});
