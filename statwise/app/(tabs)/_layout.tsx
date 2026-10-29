import React from 'react';
import { Tabs, useRouter, usePathname } from 'expo-router';
import {
  Platform, View, Text, TouchableOpacity, StyleSheet, useWindowDimensions, Image,
} from 'react-native';
import Ionicons from '@expo/vector-icons/Ionicons';
import { Colors } from '@/constants/colors';
import { useTheme } from '@/context/ThemeContext';
import { OfflineBanner } from '@/components/OfflineBanner';
import { TabIcon } from '@/components/TabIcon';

const TAB_ITEMS = [
  { name: 'index',         path: '/',             title: 'Predictions', icon: 'football-outline'    as const },
  { name: 'insights',      path: '/insights',     title: 'Insights',    icon: 'analytics-outline'   as const },
  { name: 'subscriptions', path: '/subscriptions',title: 'Plans',       icon: 'diamond-outline'     as const },
  { name: 'forum',         path: '/forum',        title: 'Community',   icon: 'chatbubbles-outline' as const },
  { name: 'profile',       path: '/profile',      title: 'Profile',     icon: 'person-outline'      as const },
];

function WebTopNav({ compact }: { compact: boolean }) {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const pathname = usePathname();
  const router = useRouter();

  return (
    <View style={[styles.webNav, { backgroundColor: C.tabBar, borderBottomColor: C.tabBarBorder }]}>
      <TouchableOpacity style={styles.brand} onPress={() => router.push('/')} activeOpacity={0.8}>
        <Image
          source={require('@/assets/images/icon.png')}
          style={styles.brandIcon}
          resizeMode="cover"
        />
        {!compact && (
          <Text style={[styles.brandText, { color: C.text }]}>StatWise</Text>
        )}
      </TouchableOpacity>

      <View style={styles.navTabs}>
        {TAB_ITEMS.map(item => {
          const isFocused =
            item.path === '/'
              ? pathname === '/'
              : pathname.startsWith(item.path);
          return (
            <TouchableOpacity
              key={item.name}
              style={[
                styles.navTab,
                compact && styles.navTabCompact,
                isFocused && { borderBottomWidth: 2, borderBottomColor: C.primary },
              ]}
              onPress={() => router.push(item.path as any)}
              activeOpacity={0.75}
            >
              <Ionicons name={item.icon} size={17} color={isFocused ? C.primary : C.textMuted} />
              {!compact && (
                <Text style={[styles.navTabText, { color: isFocused ? C.primary : C.textMuted }]}>
                  {item.title}
                </Text>
              )}
            </TouchableOpacity>
          );
        })}
      </View>
    </View>
  );
}

export default function TabLayout() {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const { width } = useWindowDimensions();

  const isWeb = Platform.OS === 'web';
  const showTopNav = isWeb && width >= 600;
  const compact = isWeb && width < 860;

  // Mobile always shows the tab bar; web hides it behind a top nav on wide screens.
  const showTabBar = isWeb ? !showTopNav : true;

  const tabBarStyle = {
    backgroundColor: 'transparent',
    borderTopColor: 'transparent',
    borderTopWidth: 0,
    height: isWeb ? 84 : undefined,
    paddingBottom: isWeb ? 34 : undefined,
    display: showTabBar ? ('flex' as const) : ('none' as const),
    position: 'absolute' as const,
    bottom: 0,
    left: 0,
    right: 0,
  };

  return (
    <View style={{ flex: 1, backgroundColor: C.background }}>
      {showTopNav && <WebTopNav compact={compact} />}

      {/* Plan 3.4 — offline / slow-connection indicator on every tab. */}
      <OfflineBanner />

      <Tabs
        screenOptions={{
          headerShown: false,
          tabBarStyle,
          tabBarActiveTintColor: C.primary,
          tabBarInactiveTintColor: C.textMuted,
          tabBarLabelStyle: { fontSize: 11, fontFamily: 'Inter_500Medium', marginBottom: 2 },
        }}
      >
        <Tabs.Screen
          name="index"
          options={{
            title: 'Predictions',
            tabBarIcon: ({ color, size, focused }) => (
              <TabIcon name="football-outline" color={color} size={size} focused={focused} />
            ),
          }}
        />
        <Tabs.Screen
          name="insights"
          options={{
            title: 'Insights',
            tabBarIcon: ({ color, size, focused }) => (
              <TabIcon name="analytics-outline" color={color} size={size} focused={focused} />
            ),
          }}
        />
        <Tabs.Screen
          name="subscriptions"
          options={{
            title: 'Plans',
            tabBarIcon: ({ color, size, focused }) => (
              <TabIcon name="diamond-outline" color={color} size={size} focused={focused} />
            ),
          }}
        />
        <Tabs.Screen
          name="forum"
          options={{
            title: 'Community',
            tabBarIcon: ({ color, size, focused }) => (
              <TabIcon name="chatbubbles-outline" color={color} size={size} focused={focused} />
            ),
          }}
        />
        <Tabs.Screen
          name="profile"
          options={{
            title: 'Profile',
            tabBarIcon: ({ color, size, focused }) => (
              <TabIcon name="person-outline" color={color} size={size} focused={focused} />
            ),
          }}
        />
      </Tabs>
    </View>
  );
}

const styles = StyleSheet.create({
  webNav: {
    height: 64,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 20,
    borderBottomWidth: 1,
  },
  brand: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginRight: 24,
  },
  brandIcon: {
    width: 30,
    height: 30,
    borderRadius: 8,
  },
  brandText: {
    fontSize: 18,
    fontFamily: 'Inter_700Bold',
    letterSpacing: -0.5,
  },
  navTabs: {
    flexDirection: 'row',
    flex: 1,
  },
  navTab: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 14,
    height: 64,
    borderBottomWidth: 0,
  },
  navTabCompact: {
    paddingHorizontal: 10,
    gap: 0,
  },
  navTabText: {
    fontSize: 14,
    fontFamily: 'Inter_500Medium',
  },
});
