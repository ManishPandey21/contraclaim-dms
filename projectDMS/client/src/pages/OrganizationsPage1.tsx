
import React, { useState } from 'react';
import { Building, Phone, Mail, MapPin, Users, FileText, ArrowUpRight } from 'lucide-react';
import AddEntityButton from '@/components/common/AddEntityButton';
import { useToast } from "@/hooks/use-toast";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Link } from 'react-router-dom';

// Sample organization data
const sampleOrganizations = [
  {
    id: 'org1',
    name: 'ABC Corp',
    panNumber: 'ABCDE1234F',
    gstNumber: '27ABCDE1234F1Z5',
    address: '123 Business Park, Main Street',
    city: 'Mumbai',
    state: 'Maharashtra',
    pinCode: '400001',
    adminName: 'Rajesh Kumar',
    adminEmail: 'rajesh@abccorp.com',
    adminContact: '9876543210',
    billing: 'yes',
    projectsCount: 5,
    employeesCount: 120,
    lettersCount: 45
  },
  {
    id: 'org2',
    name: 'XYZ Ltd',
    panNumber: 'XYZIE5678G',
    gstNumber: '29XYZIE5678G1Z2',
    address: '456 Tech Hub, IT Park',
    city: 'Bangalore',
    state: 'Karnataka',
    pinCode: '560001',
    adminName: 'Priya Sharma',
    adminEmail: 'priya@xyzltd.com',
    adminContact: '8765432109',
    billing: 'yes',
    projectsCount: 8,
    employeesCount: 200,
    lettersCount: 78
  },
  {
    id: 'org3',
    name: 'Global Industries',
    panNumber: 'GLBID7890H',
    gstNumber: '32GLBID7890H1Z7',
    address: '789 Industrial Area, Phase 2',
    city: 'Delhi',
    state: 'Delhi',
    pinCode: '110001',
    adminName: 'Vikram Singh',
    adminEmail: 'vikram@globalind.com',
    adminContact: '7654321098',
    billing: 'yes',
    projectsCount: 12,
    employeesCount: 350,
    lettersCount: 125
  },
  {
    id: 'org4',
    name: 'Sunshine Enterprises',
    panNumber: 'SUNSH2345J',
    gstNumber: '33SUNSH2345J1Z9',
    address: '234 Corporate Tower, Business District',
    city: 'Chennai',
    state: 'Tamil Nadu',
    pinCode: '600001',
    adminName: 'Ananya Reddy',
    adminEmail: 'ananya@sunshine.com',
    adminContact: '6543210987',
    billing: 'no',
    projectsCount: 3,
    employeesCount: 75,
    lettersCount: 28
  },
  {
    id: 'org5',
    name: 'Tech Solutions',
    panNumber: 'TECHS6789K',
    gstNumber: '24TECHS6789K1Z3',
    address: '567 Innovation Center, Tech Park',
    city: 'Pune',
    state: 'Maharashtra',
    pinCode: '411001',
    adminName: 'Arjun Menon',
    adminEmail: 'arjun@techsolutions.com',
    adminContact: '9876123450',
    billing: 'yes',
    projectsCount: 7,
    employeesCount: 180,
    lettersCount: 67
  }
];

const OrganizationsPage = () => {
  const { toast } = useToast();
  const [organizations, setOrganizations] = useState(sampleOrganizations);
  const [searchQuery, setSearchQuery] = useState('');

  const handleAddOrganization = (orgData: any) => {
    const newOrg = {
      id: `org${organizations.length + 1}`,
      ...orgData,
      projectsCount: 0,
      employeesCount: 0,
      lettersCount: 0
    };
    
    setOrganizations([...organizations, newOrg]);
    
    toast({
      title: "Organization Created",
      description: `Successfully created organization: ${orgData.name}`,
    });
  };

  const filteredOrganizations = organizations.filter(org => 
    org.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    org.city.toLowerCase().includes(searchQuery.toLowerCase()) ||
    org.state.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="container mx-auto py-8 animate-fade-in">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold">Organizations</h1>
        <AddEntityButton 
          entityName="Organization"
          icon={<Building size={16} className="mr-1" />}
          onAdd={handleAddOrganization}
          fields={[
            // Column 1
            { id: 'name', label: 'Organization Name', required: true, column: 1 },
            { id: 'panNumber', label: 'PAN Number (Unique)', required: true, column: 1 },
            { id: 'gstNumber', label: 'GST Number', column: 1 },
            { id: 'address', label: 'Address', required: true, column: 1 },
            { id: 'city', label: 'City', required: true, column: 1 },
            
            // Column 2
            { id: 'state', label: 'State', required: true, column: 2 },
            { id: 'pinCode', label: 'PIN Code', required: true, column: 2 },
            { id: 'adminName', label: 'Admin Name', required: true, column: 2 },
            { id: 'adminEmail', label: 'Email ID', type: 'email', required: true, column: 2 },
            { id: 'adminContact', label: 'Contact Number', required: true, column: 2 },
            { id: 'billing', label: 'Organization Billing', type: 'radio', options: [
              { label: 'Yes', value: 'yes' },
              { label: 'No', value: 'no' }
            ], required: true, column: 2 }
          ]}
          twoColumnLayout={true}
        />
      </div>
      
      {/* Search input */}
      <div className="mb-6">
        <div className="relative">
          <input
            type="text"
            placeholder="Search organizations..."
            className="w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          <div className="absolute right-3 top-2.5">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </div>
        </div>
      </div>
      
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {filteredOrganizations.length > 0 ? (
          filteredOrganizations.map((org) => (
            <Card key={org.id} className="overflow-hidden hover:shadow-lg transition-shadow duration-300">
              <CardContent className="p-0">
                <div className="bg-gradient-to-r from-blue-500 to-blue-600 p-4">
                  <h2 className="text-xl font-bold text-white">{org.name}</h2>
                  <Badge className="mt-2 bg-blue-700">{org.panNumber}</Badge>
                </div>
                <div className="p-4 space-y-3">
                  <div className="flex items-start gap-2">
                    <MapPin size={16} className="mt-1 text-gray-500" />
                    <div>
                      <p className="text-sm">{org.address}</p>
                      <p className="text-sm">{org.city}, {org.state} - {org.pinCode}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Phone size={16} className="text-gray-500" />
                    <p className="text-sm">{org.adminContact}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Mail size={16} className="text-gray-500" />
                    <p className="text-sm">{org.adminEmail}</p>
                  </div>
                  
                  <div className="grid grid-cols-3 gap-2 mt-4 border-t pt-3">
                    <div className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <FileText size={14} className="text-blue-500" />
                        <span className="font-bold">{org.projectsCount}</span>
                      </div>
                      <p className="text-xs text-gray-500">Projects</p>
                    </div>
                    <div className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <Users size={14} className="text-blue-500" />
                        <span className="font-bold">{org.employeesCount}</span>
                      </div>
                      <p className="text-xs text-gray-500">Employees</p>
                    </div>
                    <div className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <Mail size={14} className="text-blue-500" />
                        <span className="font-bold">{org.lettersCount}</span>
                      </div>
                      <p className="text-xs text-gray-500">Letters</p>
                    </div>
                  </div>
                  
                  <div className="mt-3 flex justify-end">
                    <Button size="sm" asChild>
                      <Link to={`/projects?org=${org.id}`}>
                        View Projects <ArrowUpRight className="ml-1 h-3 w-3" />
                      </Link>
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))
        ) : (
          <div className="col-span-3 flex items-center justify-center h-64 bg-gray-100 rounded-lg">
            <p className="text-gray-500">No organizations found matching your search</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default OrganizationsPage;
