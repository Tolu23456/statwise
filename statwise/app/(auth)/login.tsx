import React, { useState, useRef } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  KeyboardAvoidingView, Platform, ScrollView, useColorScheme,
  ActivityIndicator, TextInput as TextInputType,
} from 'react-native';
import { Link } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { Colors } from '@/constants/colors';
import { supabase } from '@/lib/supabase';

type FieldErrors = {
  email?: string;
  password?: string;
  general?: string;
};

function parseAuthError(message: string, email: string): FieldErrors {
  const msg = message.toLowerCase();
  if (msg.includes('invalid login credentials') || msg.includes('invalid credentials')) {
    return {
      general: 'Email or password is incorrect.',
      email: 'Double-check this email address.',
      password: 'Double-check your password.',
    };
  }
  if (msg.includes('user not found') || msg.includes('no user found')) {
    return { email: 'No account found with this email address.' };
  }
  if (msg.includes('email not confirmed') || msg.includes('email not verified') || msg.includes('confirm your email')) {
    return {
      general: `Your email isn't verified yet. Check your inbox at ${email} for the verification link.`,
    };
  }
  if (msg.includes('invalid email') || msg.includes('email is invalid')) {
    return { email: "This doesn't look like a valid email address." };
  }
  if (msg.includes('too many') || msg.includes('rate limit')) {
    return { general: 'Too many attempts. Please wait a minute and try again.' };
  }
  if (msg.includes('network') || msg.includes('fetch') || msg.includes('connection')) {
    return { general: 'Connection failed. Check your internet and try again.' };
  }
  if (msg.includes('disabled') || (msg.includes('account') && msg.includes('locked'))) {
    return { general: 'This account has been disabled. Please contact support.' };
  }
  return { general: message };
}

function isValidEmail(email: string) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
}

const FormWrapper = ({ children, onSubmit }: { children: React.ReactNode; onSubmit: () => void }) => {
  if (Platform.OS === 'web') {
    return (
      <form
        onSubmit={(e: any) => { e.preventDefault(); onSubmit(); }}
        style={{ width: '100%' }}
        autoComplete="on"
      >
        {children}
      </form>
    );
  }
  return <View style={{ width: '100%' }}>{children}</View>;
};

export default function LoginScreen() {
  const scheme = useColorScheme() ?? 'dark';
  const C = Colors[scheme];
  const insets = useSafeAreaInsets();
  const passwordRef = useRef<TextInputType>(null);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});

  function clearFieldError(field: keyof FieldErrors) {
    if (errors[field]) setErrors(prev => ({ ...prev, [field]: undefined }));
  }

  function validateLocally(): boolean {
    const newErrors: FieldErrors = {};
    if (!email.trim()) {
      newErrors.email = 'Email is required.';
    } else if (!isValidEmail(email)) {
      newErrors.email = 'Enter a valid email address (e.g. you@example.com).';
    }
    if (!password) {
      newErrors.password = 'Password is required.';
    }
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }

  async function handleLogin() {
    setErrors({});
    if (!validateLocally()) {
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
      return;
    }
    setLoading(true);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    const { error } = await supabase.auth.signInWithPassword({
      email: email.trim(),
      password,
    });
    setLoading(false);
    if (error) {
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      setErrors(parseAuthError(error.message, email.trim()));
    }
  }

  async function handleGoogleSignIn() {
    setGoogleLoading(true);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    try {
      const redirectTo = Platform.OS === 'web'
        ? (typeof window !== 'undefined' ? window.location.origin + '/' : undefined)
        : undefined;
      const { error } = await supabase.auth.signInWithOAuth({
        provider: 'google',
        options: { redirectTo },
      });
      if (error) {
        setErrors({ general: error.message });
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      }
    } catch (e) {
      setErrors({ general: 'Google sign-in failed. Please try again.' });
    } finally {
      setGoogleLoading(false);
    }
  }

  const emailHasError = !!errors.email;
  const passwordHasError = !!errors.password;

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: C.background }}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
    >
      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          { paddingTop: insets.top + 40, paddingBottom: insets.bottom + 40 },
        ]}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.inner}>
          <View style={styles.header}>
            <View style={styles.logoRow}>
              <View style={[styles.logoIcon, { backgroundColor: C.primaryLight }]}>
                <Ionicons name="stats-chart" size={22} color={C.primary} />
              </View>
              <Text style={[styles.logo, { color: C.primary }]}>StatWise</Text>
            </View>
            <Text style={[styles.tagline, { color: C.textSecondary }]}>
              AI-Powered Sports Predictions
            </Text>
          </View>

          <FormWrapper onSubmit={handleLogin}>
            <View style={[styles.card, { backgroundColor: C.card, borderColor: C.border }]}>
              <Text style={[styles.title, { color: C.text }]}>Welcome back</Text>
              <Text style={[styles.subtitle, { color: C.textSecondary }]}>
                Sign in to your account
              </Text>

              {errors.general && (
                <View style={[styles.generalError, { backgroundColor: C.dangerLight, borderColor: C.danger }]}>
                  <Ionicons name="alert-circle-outline" size={16} color={C.danger} />
                  <Text style={[styles.generalErrorText, { color: C.danger }]}>{errors.general}</Text>
                </View>
              )}

              <Text style={[styles.label, { color: C.textSecondary }]}>Email</Text>
              <TextInput
                style={[
                  styles.input,
                  {
                    backgroundColor: emailHasError ? C.dangerLight : C.inputBg,
                    borderColor: emailHasError ? C.danger : C.border,
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
                autoComplete="email"
                onSubmitEditing={() => passwordRef.current?.focus()}
              />
              {errors.email && <FieldErrorMsg text={errors.email} color={C.danger} />}

              <Text style={[styles.label, { color: C.textSecondary }]}>Password</Text>
              <View style={styles.passwordWrapper}>
                <TextInput
                  ref={passwordRef}
                  style={[
                    styles.passwordInput,
                    {
                      backgroundColor: passwordHasError ? C.dangerLight : C.inputBg,
                      borderColor: passwordHasError ? C.danger : C.border,
                      color: C.text,
                    },
                  ]}
                  placeholder="Your password"
                  placeholderTextColor={C.placeholder}
                  value={password}
                  onChangeText={v => { setPassword(v); clearFieldError('password'); clearFieldError('general'); }}
                  secureTextEntry={!showPassword}
                  returnKeyType="done"
                  autoComplete="current-password"
                  onSubmitEditing={handleLogin}
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

              <Link href="/(auth)/forgot-password" asChild>
                <TouchableOpacity style={styles.forgotRow}>
                  <Text style={[styles.forgotText, { color: C.primary }]}>Forgot password?</Text>
                </TouchableOpacity>
              </Link>

              <TouchableOpacity
                style={[styles.button, { backgroundColor: C.primary, opacity: loading ? 0.7 : 1 }]}
                onPress={handleLogin}
                disabled={loading}
              >
                {loading ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <Text style={styles.buttonText}>Sign In</Text>
                )}
              </TouchableOpacity>

              <View style={styles.dividerRow}>
                <View style={[styles.divider, { backgroundColor: C.border }]} />
                <Text style={[styles.dividerText, { color: C.textMuted }]}>or</Text>
                <View style={[styles.divider, { backgroundColor: C.border }]} />
              </View>

              <TouchableOpacity
                style={[styles.googleBtn, { borderColor: C.border, backgroundColor: C.inputBg, opacity: googleLoading ? 0.7 : 1 }]}
                onPress={handleGoogleSignIn}
                disabled={googleLoading}
                activeOpacity={0.8}
              >
                {googleLoading ? (
                  <ActivityIndicator color={C.text} size="small" />
                ) : (
                  <>
                    <Ionicons name="logo-google" size={20} color="#DB4437" />
                    <Text style={[styles.googleBtnText, { color: C.text }]}>Continue with Google</Text>
                  </>
                )}
              </TouchableOpacity>
            </View>
          </FormWrapper>

          <View style={styles.footer}>
            <Text style={[styles.footerText, { color: C.textSecondary }]}>
              Don't have an account?{' '}
            </Text>
            <Link href="/(auth)/signup" asChild>
              <TouchableOpacity>
                <Text style={[styles.footerLink, { color: C.primary }]}>Sign Up</Text>
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
  header: { alignItems: 'center', marginBottom: 36 },
  logoRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 8 },
  logoIcon: { width: 42, height: 42, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  logo: { fontSize: 36, fontFamily: 'Inter_700Bold', letterSpacing: -1 },
  tagline: { fontSize: 14, fontFamily: 'Inter_400Regular' },
  card: { borderRadius: 20, borderWidth: 1, padding: 24, marginBottom: 24, width: '100%' },
  title: { fontSize: 24, fontFamily: 'Inter_700Bold', marginBottom: 6 },
  subtitle: { fontSize: 15, fontFamily: 'Inter_400Regular', marginBottom: 20 },
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
  forgotRow: { alignSelf: 'flex-end', marginBottom: 20, marginTop: 4 },
  forgotText: { fontSize: 14, fontFamily: 'Inter_500Medium' },
  button: { borderRadius: 14, padding: 16, alignItems: 'center', marginTop: 4 },
  buttonText: { color: '#fff', fontSize: 16, fontFamily: 'Inter_600SemiBold' },
  dividerRow: { flexDirection: 'row', alignItems: 'center', gap: 12, marginVertical: 16 },
  divider: { flex: 1, height: 1 },
  dividerText: { fontSize: 13, fontFamily: 'Inter_400Regular' },
  googleBtn: {
    borderRadius: 14, borderWidth: 1, padding: 14,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10,
  },
  googleBtnText: { fontSize: 15, fontFamily: 'Inter_600SemiBold' },
  footer: { flexDirection: 'row', justifyContent: 'center', alignItems: 'center' },
  footerText: { fontSize: 15, fontFamily: 'Inter_400Regular' },
  footerLink: { fontSize: 15, fontFamily: 'Inter_600SemiBold' },
});
