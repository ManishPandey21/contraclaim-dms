#!/usr/bin/env python3
"""
PDF Bulk Downloader
A Python script to download all PDF files from a given webpage
"""

import os
import sys
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
from pathlib import Path

class PDFDownloader:
    def __init__(self, url, output_folder="downloaded_pdfs"):
        self.url = url
        self.output_folder = output_folder
        self.pdf_links = []
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

    def create_output_folder(self):
        """Create output folder if it doesn't exist"""
        Path(self.output_folder).mkdir(parents=True, exist_ok=True)
        print(f"📁 Output folder: {os.path.abspath(self.output_folder)}")

    def fetch_page(self):
        """Fetch the webpage content"""
        try:
            print(f"🌐 Fetching webpage: {self.url}")
            response = requests.get(self.url, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as e:
            print(f"❌ Error fetching webpage: {e}")
            sys.exit(1)

    def extract_pdf_links(self, html_content):
        """Extract all PDF links from the webpage"""
        soup = BeautifulSoup(html_content, 'html.parser')

        # Find all anchor tags
        all_links = soup.find_all('a', href=True)

        for link in all_links:
            href = link.get('href', '')

            # Check if the link points to a PDF
            if href.lower().endswith('.pdf'):
                # Convert relative URLs to absolute URLs
                absolute_url = urljoin(self.url, href)

                # Get the filename from URL or link text
                filename = os.path.basename(urlparse(absolute_url).path)
                if not filename:
                    filename = f"document_{len(self.pdf_links) + 1}.pdf"

                self.pdf_links.append({
                    'url': absolute_url,
                    'filename': filename,
                    'link_text': link.get_text(strip=True)[:50]  # First 50 chars
                })

        return len(self.pdf_links)

    def display_found_pdfs(self):
        """Display all found PDF links"""
        if not self.pdf_links:
            print("\n❌ No PDF files found on this webpage.")
            return False

        print(f"\n✅ Found {len(self.pdf_links)} PDF file(s):")
        print("-" * 80)
        for idx, pdf in enumerate(self.pdf_links, 1):
            print(f"{idx}. {pdf['filename']}")
            print(f"   Link text: {pdf['link_text']}")
            print(f"   URL: {pdf['url']}")
            print()
        return True

    def download_pdf(self, pdf_info, index, total):
        """Download a single PDF file"""
        try:
            print(f"⬇️  [{index}/{total}] Downloading: {pdf_info['filename']}")

            response = requests.get(pdf_info['url'], headers=self.headers, timeout=60, stream=True)
            response.raise_for_status()

            # Get file size
            file_size = int(response.headers.get('content-length', 0))
            file_size_mb = file_size / (1024 * 1024) if file_size > 0 else 0

            # Save the PDF
            output_path = os.path.join(self.output_folder, pdf_info['filename'])

            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            print(f"   ✅ Success! Size: {file_size_mb:.2f} MB")
            return True

        except Exception as e:
            print(f"   ❌ Failed: {e}")
            return False

    def download_all(self):
        """Download all found PDFs"""
        if not self.pdf_links:
            return

        print(f"\n{'='*80}")
        print(f"Starting download of {len(self.pdf_links)} PDF file(s)...")
        print(f"{'='*80}\n")

        success_count = 0
        failed_count = 0

        for idx, pdf_info in enumerate(self.pdf_links, 1):
            if self.download_pdf(pdf_info, idx, len(self.pdf_links)):
                success_count += 1
            else:
                failed_count += 1

            # Small delay between downloads to be respectful
            if idx < len(self.pdf_links):
                time.sleep(0.5)

        # Summary
        print(f"\n{'='*80}")
        print(f"📊 Download Summary:")
        print(f"   ✅ Successful: {success_count}")
        print(f"   ❌ Failed: {failed_count}")
        print(f"   📁 Files saved to: {os.path.abspath(self.output_folder)}")
        print(f"{'='*80}")

def main():
    print("="*80)
    print(" PDF Bulk Downloader ".center(80, "="))
    print("="*80)

    # Get URL from user
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = input("\n🔗 Enter the webpage URL: ").strip()

    if not url:
        print("❌ No URL provided. Exiting.")
        sys.exit(1)

    # Optional: custom output folder
    output_folder = input("📁 Enter output folder name (press Enter for 'downloaded_pdfs'): ").strip()
    if not output_folder:
        output_folder = "downloaded_pdfs"

    # Create downloader instance
    downloader = PDFDownloader(url, output_folder)

    # Create output folder
    downloader.create_output_folder()

    # Fetch webpage
    html_content = downloader.fetch_page()

    # Extract PDF links
    pdf_count = downloader.extract_pdf_links(html_content)

    # Display found PDFs
    if not downloader.display_found_pdfs():
        sys.exit(0)

    # Ask for confirmation
    print("-" * 80)
    choice = input("\n📥 Do you want to download all these PDFs? (yes/no): ").strip().lower()

    if choice in ['yes', 'y']:
        downloader.download_all()
    else:
        print("\n❌ Download cancelled.")

if __name__ == "__main__":
    main()
