import React, { useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, TextInput,
  Switch, Alert, ActivityIndicator, Platform, Share,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';
import * as Haptics from 'expo-haptics';
import { Image } from 'expo-image';
import { useRouter } from 'expo-router';
import { Colors, TierBadgeColors } from '@/constants/colors';
import { supabase, ReferralCode } from '@/lib/supabase';
import { useAuth } from '@/context/AuthContext';
import { useTheme, ThemeMode } from '@/context/ThemeContext';
import { useQuery } from '@tanstack/react-query';

const THEME_OPTIONS: { value: ThemeMode; icon: string; label: string }[] = [
  { value: 'system', icon: 'phone-portrait-outline', label: 'Auto' },
  { value: 'light', icon: 'sunny', label: 'Light' },
  { value: 'dark', icon: 'moon', label: 'Dark' },
];

const TIER_REWARD_POINTS: Record<string, number> = {
  'Free Tier': 100,
  'Premium Tier': 500,
  'VIP Tier': 2000,
  'VVIP Tier': 5000,
};

export default function ProfileScreen() {
  const { scheme, themeMode, setThemeMode } = useTheme();
  const C = Colors[scheme];
  const insets = useSafeAreaInsets();
  const { user, profile, signOut, refreshProfile } = useAuth();
  const router = useRouter();

  const [editingName, setEditingName] = useState(false);
  const [displayName, setDisplayName] = useState(profile?.display_name ?? '');
  const [savingName, setSavingName] = useState(false);
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  const topInset = Platform.OS === 'web' ? 0 : insets.top;
  const tierColors = TierBadgeColors[profile?.current_tier ?? 'Free Tier'];

  const { data: referral } = useQuery<ReferralCode | null>({
    queryKey: ['referral', user?.id],
    queryFn: async () => {
      if (!user) return null;
      const { data } = await supabase
        .from('referral_codes')
        .select('*')
        .eq('user_id', user.id)
        .single();
      return data as ReferralCode ?? null;
    },
    enabled: !!user,
  });

  const rewardPoints = referral
    ? referral.total_referrals * (TIER_REWARD_POINTS[profile?.current_tier ?? 'Free Tier'])
    : 0;

  async function saveDisplayName() {
    if (!displayName.trim() || !user) return;
    setSavingName(true);
    await supabase
      .from('user_profiles')
      .update({ display_name: displayName.trim(), username: displayName.trim() })
      .eq('id', user.id);
    await refreshProfile();
    setSavingName(false);
    setEditingName(false);
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
  }

  async function pickAndUploadPhoto() {
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== 'granted') {
      Alert.alert('Permission Required', 'Please allow access to your photo library.');
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      allowsEditing: true, aspect: [1, 1], quality: 0.7,
    });
    if (result.canceled || !result.assets[0]) return;
    if (!user) return;

    setUploadingPhoto(true);
    let retries = 0;
    while (retries < 3) {
      try {
        const asset = result.assets[0];
        const ext = asset.uri.split('.').pop() ?? 'jpg';
        const fileName = `${user.id}.${ext}`;
        const response = await fetch(asset.uri);
        const blob = await response.blob();

        const { error: uploadError } = await supabase.storage
          .from('profile-pictures')
          .upload(fileName, blob, { upsert: true, contentType: `image/${ext}` });

        if (!uploadError) {
          const { data: urlData } = supabase.storage
            .from('profile-pictures')
            .getPublicUrl(fileName);
          await supabase
            .from('user_profiles')
            .update({ avatar_url: urlData.publicUrl })
            .eq('id', user.id);
          await refreshProfile();
          Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
          break;
        } else {
          throw uploadError;
        }
      } catch (e) {
        retries++;
        if (retries >= 3) {
          Alert.alert('Upload Failed', 'Could not upload profile picture after 3 attempts.');
        }
      }
    }
    setUploadingPhoto(false);
  }

  async function handleSignOut() {
    Alert.alert('Sign Out', 'Are you sure you want to sign out?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Sign Out',
        style: 'destructive',
        onPress: async () => {
          setSigningOut(true);
          await signOut();
        },
      },
    ]);
  }

  function getInitials(name?: string) {
    if (!name) return '?';
    return name.split(' ').map(n => n[0]).join('').toUpperCase().substring(0, 2);
  }

  function copyReferralCode() {
    if (!referral?.code) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    if (Platform.OS === 'web' && navigator.clipboard) {
      navigator.clipboard.writeText(referral.code);
      Alert.alert('Copied!', `Code "${referral.code}" copied to clipboard.`);
    } else {
      Alert.alert('Referral Code', `Your code: ${referral.code}\n\nShare this with friends to earn rewards!`);
    }
  }

  async function shareReferralCode() {
    if (!referral?.code) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    const message = `Join me on StatWise — the AI-powered football prediction app! Use my referral code ${referral.code} to get started.`;
    try {
      if (Platform.OS === 'web' && navigator.share) {
        await navigator.share({ title: 'StatWise Referral', text: message });
      } else {
        await Share.share({ message });
      }
    } catch {
      copyReferralCode();
    }
  }

  return (
    <View style={[styles.container, { backgroundColor: C.background }]}>
      <View style={[styles.header, { paddingTop: topInset + 8 }]}>
        <Text style={[styles.title, { color: C.text }]}>Profile</Text>
      </View>

      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          { paddingBottom: insets.bottom + (Platform.OS === 'web' ? 24 : 80) },
        ]}
        showsVerticalScrollIndicator={false}
      >
        <View style={[styles.profileCard, { backgroundColor: C.card, borderColor: C.border }]}>
          <TouchableOpacity style={styles.avatarWrapper} onPress={pickAndUploadPhoto} disabled={uploadingPhoto}>
            {profile?.avatar_url ? (
              <Image source={{ uri: profile.avatar_url }} style={styles.avatar} contentFit="cover" />
            ) : (
              <View style={[styles.avatarPlaceholder, { backgroundColor: C.primaryLight }]}>
                <Text style={[styles.avatarInitials, { color: C.primary }]}>
                  {getInitials(profile?.display_name)}
                </Text>
              </View>
            )}
            {uploadingPhoto ? (
              <View style={styles.avatarOverlay}>
                <ActivityIndicator color="#fff" size="small" />
              </View>
            ) : (
              <View style={[styles.avatarOverlay, { backgroundColor: 'rgba(0,0,0,0.4)' }]}>
                <Ionicons name="camera" size={16} color="#fff" />
              </View>
            )}
          </TouchableOpacity>

          {editingName ? (
            <View style={styles.nameEditRow}>
              <TextInput
                style={[styles.nameInput, { borderColor: C.primary, color: C.text, backgroundColor: C.inputBg }]}
                value={displayName}
                onChangeText={setDisplayName}
                autoFocus
                returnKeyType="done"
                onSubmitEditing={saveDisplayName}
              />
              <TouchableOpacity
                style={[styles.saveBtn, { backgroundColor: C.primary }]}
                onPress={saveDisplayName}
                disabled={savingName}
              >
                {savingName
                  ? <ActivityIndicator color="#fff" size="small" />
                  : <Ionicons name="checkmark" size={18} color="#fff" />
                }
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.cancelBtn, { backgroundColor: C.border }]}
                onPress={() => { setEditingName(false); setDisplayName(profile?.display_name ?? ''); }}
              >
                <Ionicons name="close" size={18} color={C.textSecondary} />
              </TouchableOpacity>
            </View>
          ) : (
            <TouchableOpacity style={styles.nameRow} onPress={() => setEditingName(true)}>
              <Text style={[styles.profileName, { color: C.text }]}>{profile?.display_name ?? 'User'}</Text>
              <Ionicons name="pencil" size={14} color={C.textSecondary} style={{ marginLeft: 6 }} />
            </TouchableOpacity>
          )}

          <Text style={[styles.profileEmail, { color: C.textSecondary }]}>{user?.email}</Text>
          <View style={[styles.tierBadge, { backgroundColor: tierColors.bg }]}>
            <Text style={[styles.tierText, { color: tierColors.text }]}>{profile?.current_tier ?? 'Free Tier'}</Text>
          </View>
        </View>

        {referral && (
          <View style={[styles.section, { backgroundColor: C.card, borderColor: C.border }]}>
            <Text style={[styles.sectionTitle, { color: C.text }]}>Referral Program</Text>
            <Text style={[styles.sectionDesc, { color: C.textSecondary }]}>
              Invite friends and earn reward points when they subscribe.
            </Text>
            <View style={[styles.codeRow, { backgroundColor: C.inputBg, borderColor: C.border }]}>
              <Text style={[styles.codeText, { color: C.primary }]}>{referral.code}</Text>
              <View style={styles.codeActions}>
                <TouchableOpacity
                  style={[styles.codeActionBtn, { backgroundColor: C.primaryLight }]}
                  onPress={copyReferralCode}
                  hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                >
                  <Ionicons name="copy-outline" size={17} color={C.primary} />
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.codeActionBtn, { backgroundColor: C.successLight }]}
                  onPress={shareReferralCode}
                  hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                >
                  <Ionicons name="share-social-outline" size={17} color={C.success} />
                </TouchableOpacity>
              </View>
            </View>
            <View style={styles.referralStatsRow}>
              <View style={[styles.referralStat, { backgroundColor: C.primaryLight }]}>
                <Text style={[styles.referralStatNum, { color: C.primary }]}>{referral.total_referrals}</Text>
                <Text style={[styles.referralStatLabel, { color: C.textSecondary }]}>Friends Referred</Text>
              </View>
              <View style={[styles.referralStat, { backgroundColor: C.goldLight }]}>
                <Text style={[styles.referralStatNum, { color: C.gold }]}>{rewardPoints.toLocaleString()}</Text>
                <Text style={[styles.referralStatLabel, { color: C.textSecondary }]}>Reward Points</Text>
              </View>
            </View>
            <Text style={[styles.referralHint, { color: C.textMuted }]}>
              Earn {TIER_REWARD_POINTS[profile?.current_tier ?? 'Free Tier'].toLocaleString()} points per successful referral at your current tier.
            </Text>
          </View>
        )}

        <View style={[styles.section, { backgroundColor: C.card, borderColor: C.border }]}>
          <Text style={[styles.sectionTitle, { color: C.text }]}>Appearance</Text>
          <View style={styles.themeRow}>
            {THEME_OPTIONS.map(opt => {
              const isActive = themeMode === opt.value;
              return (
                <TouchableOpacity
                  key={opt.value}
                  style={[
                    styles.themeBtn,
                    {
                      backgroundColor: isActive ? C.primary : C.inputBg,
                      borderColor: isActive ? C.primary : C.border,
                    },
                  ]}
                  onPress={() => {
                    setThemeMode(opt.value);
                    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                  }}
                >
                  <Ionicons
                    name={opt.icon as any}
                    size={20}
                    color={isActive ? '#fff' : C.textSecondary}
                  />
                  <Text style={[styles.themeBtnText, { color: isActive ? '#fff' : C.textSecondary }]}>
                    {opt.label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        </View>

        <View style={[styles.section, { backgroundColor: C.card, borderColor: C.border }]}>
          <Text style={[styles.sectionTitle, { color: C.text }]}>Settings</Text>
          <View style={[styles.settingRow, { borderBottomColor: C.border }]}>
            <Ionicons name="notifications-outline" size={20} color={C.textSecondary} />
            <Text style={[styles.settingLabel, { color: C.text }]}>Notifications</Text>
            <Switch
              value={profile?.notifications ?? true}
              onValueChange={async (val) => {
                if (!user) return;
                if (val && Platform.OS === 'web' && 'Notification' in window) {
                  await Notification.requestPermission();
                }
                await supabase.from('user_profiles').update({ notifications: val }).eq('id', user.id);
                await refreshProfile();
              }}
              trackColor={{ false: C.border, true: C.primary }}
              thumbColor="#fff"
            />
          </View>
          <View style={[styles.settingRow, { borderBottomColor: C.border }]}>
            <Ionicons name="shield-checkmark-outline" size={20} color={C.textSecondary} />
            <Text style={[styles.settingLabel, { color: C.text }]}>Account Verified</Text>
            <Ionicons
              name={user?.email_confirmed_at ? 'checkmark-circle' : 'alert-circle'}
              size={20}
              color={user?.email_confirmed_at ? C.success : C.warning}
            />
          </View>
          <TouchableOpacity
            style={[styles.settingRow, { borderBottomColor: C.border }]}
            onPress={() => router.push('/backtesting')}
          >
            <Ionicons name="stats-chart-outline" size={20} color={C.textSecondary} />
            <Text style={[styles.settingLabel, { color: C.text }]}>Prediction Accuracy</Text>
            <Ionicons name="chevron-forward" size={18} color={C.textMuted} />
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.settingRow, { borderBottomColor: C.border }]}
            onPress={() => router.push('/privacy-policy')}
          >
            <Ionicons name="document-text-outline" size={20} color={C.textSecondary} />
            <Text style={[styles.settingLabel, { color: C.text }]}>Privacy Policy</Text>
            <Ionicons name="chevron-forward" size={18} color={C.textMuted} />
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.settingRow}
            onPress={() => router.push('/terms-of-service')}
          >
            <Ionicons name="reader-outline" size={20} color={C.textSecondary} />
            <Text style={[styles.settingLabel, { color: C.text }]}>Terms of Service</Text>
            <Ionicons name="chevron-forward" size={18} color={C.textMuted} />
          </TouchableOpacity>
        </View>

        <TouchableOpacity
          style={[styles.signOutBtn, { borderColor: C.danger, backgroundColor: C.dangerLight }]}
          onPress={handleSignOut}
          disabled={signingOut}
        >
          {signingOut
            ? <ActivityIndicator color={C.danger} size="small" />
            : <>
                <Ionicons name="log-out-outline" size={20} color={C.danger} />
                <Text style={[styles.signOutText, { color: C.danger }]}>Sign Out</Text>
              </>
          }
        </TouchableOpacity>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  header: { paddingHorizontal: 16, paddingBottom: 12 },
  title: { fontSize: 22, fontFamily: 'Inter_700Bold' },
  scroll: { paddingHorizontal: 16, paddingTop: 4 },
  profileCard: {
    borderRadius: 20, borderWidth: 1, padding: 24,
    alignItems: 'center', marginBottom: 16,
  },
  avatarWrapper: { position: 'relative', marginBottom: 14 },
  avatar: { width: 80, height: 80, borderRadius: 40 },
  avatarPlaceholder: {
    width: 80, height: 80, borderRadius: 40,
    alignItems: 'center', justifyContent: 'center',
  },
  avatarInitials: { fontSize: 28, fontFamily: 'Inter_700Bold' },
  avatarOverlay: {
    position: 'absolute', bottom: 0, right: 0,
    width: 26, height: 26, borderRadius: 13,
    alignItems: 'center', justifyContent: 'center',
  },
  nameRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 4 },
  profileName: { fontSize: 20, fontFamily: 'Inter_700Bold' },
  nameEditRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 4, width: '100%' },
  nameInput: {
    flex: 1, borderRadius: 10, borderWidth: 1.5,
    padding: 10, fontSize: 16, fontFamily: 'Inter_500Medium',
  },
  saveBtn: { width: 38, height: 38, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  cancelBtn: { width: 38, height: 38, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  profileEmail: { fontSize: 14, fontFamily: 'Inter_400Regular', marginBottom: 10 },
  tierBadge: { paddingHorizontal: 14, paddingVertical: 5, borderRadius: 20 },
  tierText: { fontSize: 13, fontFamily: 'Inter_600SemiBold' },
  section: { borderRadius: 16, borderWidth: 1, padding: 16, marginBottom: 14 },
  sectionTitle: { fontSize: 17, fontFamily: 'Inter_700Bold', marginBottom: 6 },
  sectionDesc: { fontSize: 14, fontFamily: 'Inter_400Regular', marginBottom: 14 },
  codeRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    borderRadius: 12, borderWidth: 1, paddingHorizontal: 14, paddingVertical: 12, marginBottom: 12,
  },
  codeText: { fontSize: 20, fontFamily: 'Inter_700Bold', letterSpacing: 3 },
  codeActions: { flexDirection: 'row', gap: 8 },
  codeActionBtn: { width: 36, height: 36, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  referralStatsRow: { flexDirection: 'row', gap: 10, marginBottom: 10 },
  referralStat: { flex: 1, borderRadius: 12, padding: 12, alignItems: 'center', gap: 4 },
  referralStatNum: { fontSize: 22, fontFamily: 'Inter_700Bold' },
  referralStatLabel: { fontSize: 12, fontFamily: 'Inter_400Regular', textAlign: 'center' },
  referralHint: { fontSize: 12, fontFamily: 'Inter_400Regular', fontStyle: 'italic' },
  themeRow: { flexDirection: 'row', gap: 8, marginTop: 4 },
  themeBtn: {
    flex: 1, flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
    gap: 5, paddingVertical: 12, borderRadius: 12, borderWidth: 1,
  },
  themeBtnText: { fontSize: 12, fontFamily: 'Inter_500Medium' },
  settingRow: {
    flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12,
    borderBottomWidth: 1,
  },
  settingLabel: { flex: 1, fontSize: 15, fontFamily: 'Inter_400Regular' },
  signOutBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10,
    borderRadius: 16, borderWidth: 1.5, padding: 16, marginBottom: 8,
  },
  signOutText: { fontSize: 16, fontFamily: 'Inter_600SemiBold' },
});
