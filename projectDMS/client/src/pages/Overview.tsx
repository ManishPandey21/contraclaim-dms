import React from 'react';
import { Link } from 'react-router-dom';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ArrowRight, BookOpen, FileText, Users, FolderArchive, Upload, Settings, User, Shield } from 'lucide-react';

const Index = () => {
  const menuItems = [
    {
      title: "Dashboard",
      description: "View analytics and document statistics",
      icon: <FileText className="h-8 w-8 text-docsumo-blue" />,
      path: "/dashboard",
      color: "bg-blue-50"
    },
    {
      title: "Organizations",
      description: "Manage your organizations",
      icon: <Users className="h-8 w-8 text-docsumo-blue" />,
      path: "/organizations",
      color: "bg-indigo-50"
    },
    {
      title: "Projects",
      description: "View and manage projects",
      icon: <FolderArchive className="h-8 w-8 text-docsumo-blue" />,
      path: "/projects",
      color: "bg-purple-50"
    },
    {
      title: "Documents",
      description: "Browse and search all documents",
      icon: <FileText className="h-8 w-8 text-docsumo-blue" />,
      path: "/documents",
      color: "bg-green-50"
    },
    {
      title: "Learn Contractual/Legal Words",
      description: "Review today's platform-wide contractual writing words",
      icon: <BookOpen className="h-8 w-8 text-docsumo-blue" />,
      path: "/legal-words",
      color: "bg-sky-50"
    },
    {
      title: "Upload",
      description: "Upload new documents",
      icon: <Upload className="h-8 w-8 text-docsumo-blue" />,
      path: "/upload",
      color: "bg-yellow-50"
    },
    {
      title: "Register",
      description: "Register new organizations & projects",
      icon: <FolderArchive className="h-8 w-8 text-docsumo-blue" />,
      path: "/register",
      color: "bg-red-50"
    },
    {
      title: "Users",
      description: "Manage users and permissions",
      icon: <User className="h-8 w-8 text-docsumo-blue" />,
      path: "/users",
      color: "bg-orange-50"
    },
    {
      title: "Permissions",
      description: "Configure access control",
      icon: <Shield className="h-8 w-8 text-docsumo-blue" />,
      path: "/permissions",
      color: "bg-teal-50"
    },
    {
      title: "Profile",
      description: "View and edit your profile",
      icon: <User className="h-8 w-8 text-docsumo-blue" />,
      path: "/profile",
      color: "bg-cyan-50"
    }
  ];

  return (
    <div className="min-h-screen bg-gradient-to-b from-white to-gray-100">
      <div className="container mx-auto py-12 px-4">
        <div className="text-center mb-12">
          <h1 className="text-4xl md:text-5xl font-bold mb-4 text-docsumo-text">Overview</h1>
          <p className="text-xl text-gray-600 max-w-3xl mx-auto">
            A comprehensive platform for managing documents, organizations, and projects with advanced features
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {menuItems.map((item, index) => (
            <Link to={item.path} key={index} className="transition-transform hover:scale-105">
              <Card className="h-full overflow-hidden shadow-sm hover:shadow-md transition-shadow">
                <CardHeader className={`${item.color} py-6`}>
                  <div className="flex justify-between items-start">
                    <div>{item.icon}</div>
                    <ArrowRight className="h-5 w-5 text-docsumo-blue" />
                  </div>
                </CardHeader>
                <CardContent className="pt-6">
                  <CardTitle className="text-xl mb-2">{item.title}</CardTitle>
                  <CardDescription>{item.description}</CardDescription>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
};

export default Index;
