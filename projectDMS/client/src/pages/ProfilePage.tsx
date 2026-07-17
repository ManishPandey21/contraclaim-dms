import React, { useState, useEffect } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/use-toast";
import ProfileScopeSubscriptionCard from "@/components/profile/ProfileScopeSubscriptionCard";
import { enhancedApi as api } from "@/services/enhanced-api";
import {
  getUserDateFormat,
  setUserDateFormat,
  getAvailableDateFormats,
  getDateFormatLabel,
  type DateFormat,
} from "@/utils/dateFormat";

type ProfileRead = {
  id: string;
  first_name?: string | null;
  last_name?: string | null;
  email: string;
  job_title?: string | null;
  profile_photo_url?: string | null;
};

const COMMON_TIMEZONES = [
  "Asia/Kolkata",
  "UTC",
  "Europe/London",
  "America/New_York",
  "Asia/Dubai",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
];

const ProfilePage = () => {
  const { toast } = useToast();
  const [profile, setProfile] = useState<ProfileRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedPhotoFile, setSelectedPhotoFile] = useState<File | null>(null);
  const [photoPreviewUrl, setPhotoPreviewUrl] = useState<string | null>(null);

  // Security state
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  // Preferences
  const [timezone, setTimezone] = useState<string>(() => {
    try {
      return window.localStorage.getItem("user_timezone") || "Asia/Kolkata";
    } catch {
      return "Asia/Kolkata";
    }
  });

  const [dateFormat, setDateFormat] = useState<DateFormat>(() => {
    return getUserDateFormat();
  });

  useEffect(() => {
    const fetchProfile = async () => {
      try {
        const data = await api.getProfile();
        // Cast to our view model shape safely
        const p: ProfileRead = {
          id: (data as any)?.id || (data as any)?._id || "",
          first_name: (data as any)?.first_name ?? "",
          last_name: (data as any)?.last_name ?? "",
          email: (data as any)?.email ?? "",
          job_title: (data as any)?.job_title ?? "",
          profile_photo_url: (data as any)?.profile_photo_url ?? "",
        };
        setProfile(p);
      } catch (err) {
        setError("Failed to fetch profile data.");
        console.error(err);
      } finally {
        setLoading(false);
      }
    };

    fetchProfile();
  }, []);

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    if (profile) {
      setProfile({
        ...profile,
        [name]: value,
      });
    }
  };

  const handlePhotoInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] || null;
    if (!file) return;

    if (!file.type.startsWith("image/")) {
      toast({
        variant: "destructive",
        title: "Invalid file",
        description: "Please select a valid image file.",
      });
      e.target.value = "";
      return;
    }

    const maxBytes = 5 * 1024 * 1024;
    if (file.size > maxBytes) {
      toast({
        variant: "destructive",
        title: "File too large",
        description: "Image size must be 5MB or less.",
      });
      e.target.value = "";
      return;
    }

    setSelectedPhotoFile(file);
    const preview = URL.createObjectURL(file);
    setPhotoPreviewUrl(preview);
  };

  const handleSaveChanges = async () => {
    if (!profile) return;
    try {
      let uploadedPhotoUrl = profile.profile_photo_url ?? "";

      if (selectedPhotoFile) {
        const photoResp = await api.uploadMyProfilePhoto(selectedPhotoFile);
        uploadedPhotoUrl =
          (photoResp as any)?.profile_photo_url ?? uploadedPhotoUrl;
      }

      const updated = await api.updateProfile({
        first_name: profile.first_name ?? "",
        last_name: profile.last_name ?? "",
        email: profile.email,
        job_title: profile.job_title ?? "",
        profile_photo_url: uploadedPhotoUrl,
      } as any);

      const merged = {
        id: (updated as any)?.id || (updated as any)?._id || profile.id,
        first_name: (updated as any)?.first_name ?? profile.first_name,
        last_name: (updated as any)?.last_name ?? profile.last_name,
        email: (updated as any)?.email ?? profile.email,
        job_title: (updated as any)?.job_title ?? profile.job_title,
        profile_photo_url:
          (updated as any)?.profile_photo_url ??
          uploadedPhotoUrl ??
          profile.profile_photo_url,
      };

      setProfile(merged);
      setSelectedPhotoFile(null);
      setPhotoPreviewUrl(null);

      window.localStorage.removeItem("profile_cache");
      window.dispatchEvent(
        new CustomEvent("profile-updated", { detail: merged }),
      );

      toast({
        title: "Success",
        description: "Your profile has been updated successfully.",
      });
    } catch (err: any) {
      console.error(err);
      toast({
        variant: "destructive",
        title: "Error",
        description: err?.message || "Failed to update profile.",
      });
    }
  };

  const handleUpdatePassword = async () => {
    if (!currentPassword || !newPassword || !confirmPassword) {
      toast({
        variant: "destructive",
        title: "Validation",
        description: "Please fill all password fields.",
      });
      return;
    }
    if (newPassword.length < 6) {
      toast({
        variant: "destructive",
        title: "Validation",
        description: "New password must be at least 6 characters.",
      });
      return;
    }
    if (newPassword !== confirmPassword) {
      toast({
        variant: "destructive",
        title: "Validation",
        description: "New password and confirm password do not match.",
      });
      return;
    }
    try {
      await api.changeMyPassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      toast({
        title: "Password updated",
        description: "Your password has been changed successfully.",
      });
    } catch (err: any) {
      console.error(err);
      toast({
        variant: "destructive",
        title: "Error",
        description: err?.message || "Failed to update password.",
      });
    }
  };

  const handleSavePreferences = () => {
    try {
      window.localStorage.setItem("user_timezone", timezone);
      setUserDateFormat(dateFormat);
      toast({
        title: "Preferences saved",
        description: `Timezone set to ${timezone} and date format set to ${dateFormat}.`,
      });
      // Reload the page to apply date format changes across the app
      setTimeout(() => {
        window.location.reload();
      }, 1000);
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Error",
        description: "Failed to save preferences.",
      });
    }
  };

  if (loading) {
    return <div className="container mx-auto py-8">Loading profile...</div>;
  }

  if (error) {
    return <div className="container mx-auto py-8 text-red-500">{error}</div>;
  }

  if (!profile) {
    return (
      <div className="container mx-auto py-8">Could not load profile.</div>
    );
  }

  const initials = `${(profile.first_name || "").charAt(0)}${(
    profile.last_name || ""
  ).charAt(0)}`;

  return (
    <div className="container mx-auto py-8 animate-fade-in">
      <h1 className="mb-6 text-2xl font-bold">My Profile</h1>
      <ProfileScopeSubscriptionCard />
      <div className="flex flex-col items-center md:flex-row md:items-start gap-8">
        <div className="w-full md:w-1/3">
          <Card>
            <CardHeader className="items-center text-center">
              <Avatar className="w-24 h-24">
                <AvatarImage
                  src={photoPreviewUrl || profile.profile_photo_url || ""}
                  alt="User profile"
                />
                <AvatarFallback>{initials || "U"}</AvatarFallback>
              </Avatar>
              <CardTitle className="mt-4">{`${profile.first_name || ""} ${
                profile.last_name || ""
              }`}</CardTitle>
              <CardDescription>{profile.email}</CardDescription>
            </CardHeader>
            <CardContent className="text-center">
              <p className="text-sm text-muted-foreground mb-4">Account</p>
              <Button className="w-full mb-2">Edit Profile</Button>
              {/* The security actions are available in the Security tab */}
            </CardContent>
          </Card>
        </div>

        <div className="w-full md:w-2/3">
          <Tabs defaultValue="profile" className="w-full">
            <TabsList className="grid w-full grid-cols-3">
              <TabsTrigger value="profile">Profile</TabsTrigger>
              <TabsTrigger value="security">Security</TabsTrigger>
              <TabsTrigger value="preferences">Preferences</TabsTrigger>
            </TabsList>

            <TabsContent value="profile" className="mt-6">
              <Card>
                <CardHeader>
                  <CardTitle>Profile Information</CardTitle>
                  <CardDescription>
                    Update your personal information and contact details.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label htmlFor="first_name">First Name</Label>
                      <Input
                        id="first_name"
                        name="first_name"
                        value={profile.first_name || ""}
                        onChange={handleInputChange}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="last_name">Last Name</Label>
                      <Input
                        id="last_name"
                        name="last_name"
                        value={profile.last_name || ""}
                        onChange={handleInputChange}
                      />
                    </div>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="email">Email</Label>
                    <Input
                      id="email"
                      name="email"
                      type="email"
                      value={profile.email}
                      onChange={handleInputChange}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="job_title">Job Title</Label>
                    <Input
                      id="job_title"
                      name="job_title"
                      value={profile.job_title || ""}
                      onChange={handleInputChange}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="profile_photo">Profile Photo</Label>
                    <Input
                      id="profile_photo"
                      name="profile_photo"
                      type="file"
                      accept="image/*"
                      onChange={handlePhotoInputChange}
                    />
                    <p className="text-xs text-muted-foreground">
                      Upload JPG, PNG, WEBP, or GIF up to 5MB.
                    </p>
                  </div>
                </CardContent>
                <CardFooter>
                  <Button onClick={handleSaveChanges}>Save Changes</Button>
                </CardFooter>
              </Card>
            </TabsContent>

            <TabsContent value="security" className="mt-6">
              <Card>
                <CardHeader>
                  <CardTitle>Security Settings</CardTitle>
                  <CardDescription>Change your password.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="currentPassword">Current Password</Label>
                    <Input
                      id="currentPassword"
                      type="password"
                      value={currentPassword}
                      onChange={(e) => setCurrentPassword(e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="newPassword">New Password</Label>
                    <Input
                      id="newPassword"
                      type="password"
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="confirmPassword">Confirm Password</Label>
                    <Input
                      id="confirmPassword"
                      type="password"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                    />
                  </div>
                </CardContent>
                <CardFooter>
                  <Button onClick={handleUpdatePassword}>
                    Update Password
                  </Button>
                </CardFooter>
              </Card>
            </TabsContent>

            <TabsContent value="preferences" className="mt-6">
              <Card>
                <CardHeader>
                  <CardTitle>User Preferences</CardTitle>
                  <CardDescription>
                    Configure your application preferences.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-4">
                    <div className="space-y-2">
                      <Label htmlFor="timezone">Timezone</Label>
                      <select
                        id="timezone"
                        className="w-full border rounded-md px-3 py-2"
                        value={timezone}
                        onChange={(e) => setTimezone(e.target.value)}
                      >
                        {COMMON_TIMEZONES.map((tz) => (
                          <option key={tz} value={tz}>
                            {tz}
                          </option>
                        ))}
                      </select>
                      <p className="text-xs text-muted-foreground">
                        Dates and times will be shown using this timezone.
                      </p>
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="dateFormat">Date Format</Label>
                      <select
                        id="dateFormat"
                        className="w-full border rounded-md px-3 py-2"
                        value={dateFormat}
                        onChange={(e) =>
                          setDateFormat(e.target.value as DateFormat)
                        }
                      >
                        {getAvailableDateFormats().map((format) => (
                          <option key={format} value={format}>
                            {getDateFormatLabel(format)}
                          </option>
                        ))}
                      </select>
                      <p className="text-xs text-muted-foreground">
                        Choose how dates should be displayed throughout the
                        application. Example: {new Date().toLocaleDateString()}{" "}
                        will be shown as{" "}
                        {dateFormat === "dd/mm/yyyy"
                          ? `${String(new Date().getDate()).padStart(
                              2,
                              "0",
                            )}/${String(new Date().getMonth() + 1).padStart(
                              2,
                              "0",
                            )}/${new Date().getFullYear()}`
                          : dateFormat === "mm/dd/yyyy"
                            ? `${String(new Date().getMonth() + 1).padStart(
                                2,
                                "0",
                              )}/${String(new Date().getDate()).padStart(
                                2,
                                "0",
                              )}/${new Date().getFullYear()}`
                            : `${new Date().getFullYear()}-${String(
                                new Date().getMonth() + 1,
                              ).padStart(2, "0")}-${String(
                                new Date().getDate(),
                              ).padStart(2, "0")}`}
                      </p>
                    </div>
                  </div>
                </CardContent>
                <CardFooter>
                  <Button onClick={handleSavePreferences}>
                    Save Preferences
                  </Button>
                </CardFooter>
              </Card>
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </div>
  );
};

export default ProfilePage;
