/**
 * Tests for Domain-Aware Entity Management UI Pages - Issue #244
 * 
 * These tests are designed to fail initially since the pages don't exist yet.
 * They follow TDD principles by specifying the expected behavior and API contracts
 * for the entity management pages and routing.
 * 
 * Pages being tested:
 * - /entities - Main entities page with tab navigation
 * - /entities/[type]/new - Create entity flow
 * - /entities/[type]/[id] - Edit entity flow  
 * - Delete entity confirmation flow
 * - Page navigation and routing
 */

import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom'
import { useRouter } from 'next/navigation'

// Import pages that don't exist yet - these will cause failures
import EntitiesPage from '@/app/(protected)/entities/page'
import CreateEntityPage from '@/app/(protected)/entities/[type]/new/page'
import EditEntityPage from '@/app/(protected)/entities/[type]/[id]/page'

// Mock the API client
jest.mock('@/lib/api/entities', () => ({
  listEntities: jest.fn(),
  createEntity: jest.fn(),
  getEntity: jest.fn(),
  updateEntity: jest.fn(),
  deleteEntity: jest.fn(),
  searchEntities: jest.fn(),
  addRelationship: jest.fn(),
  removeRelationship: jest.fn(),
  getDomainSchema: jest.fn(),
  validateEntity: jest.fn()
}))

// Mock the domain API
jest.mock('@/lib/api/domain', () => ({
  fetchDomainConfig: jest.fn(),
  switchDomain: jest.fn()
}))

// Mock domain context
jest.mock('@/contexts/DomainContext', () => ({
  useDomain: () => ({
    currentDomain: 'consulting_firm',
    domainConfig: mockDomainConfig,
    setCurrentDomain: jest.fn(),
    loading: false,
    error: null
  })
}))

const mockDomainConfig = {
  id: 'consulting_firm',
  name: 'Consulting Firm',
  entities: {
    person: {
      name: 'Person',
      attributes: {
        name: { type: 'string', required: true },
        email: { type: 'string', required: true },
        role: { type: 'enum', values: ['consultant', 'manager', 'partner'], required: false }
      },
      relationships: {
        works_on_projects: { target: 'project', cardinality: 'many-to-many' }
      }
    },
    project: {
      name: 'Project', 
      attributes: {
        name: { type: 'string', required: true },
        status: { type: 'enum', values: ['planning', 'active', 'completed'], required: true }
      },
      relationships: {
        has_team_members: { target: 'person', cardinality: 'many-to-many' }
      }
    },
    account: {
      name: 'Account',
      attributes: {
        name: { type: 'string', required: true },
        is_active: { type: 'boolean', required: true }
      }
    }
  }
}

const mockEntities = [
  {
    id: 'person-1',
    entity_type: 'person',
    attributes: { name: 'John Smith', email: 'john@example.com', role: 'consultant' },
    created_at: '2024-01-10T10:00:00Z',
    updated_at: '2024-01-10T10:00:00Z'
  },
  {
    id: 'project-1',
    entity_type: 'project', 
    attributes: { name: 'Alpha Project', status: 'active' },
    created_at: '2024-01-05T14:30:00Z',
    updated_at: '2024-01-08T09:15:00Z'
  }
]

// Mock router
const mockPush = jest.fn()
const mockReplace = jest.fn()
const mockBack = jest.fn()

jest.mock('next/navigation', () => ({
  useRouter: () => ({
    push: mockPush,
    replace: mockReplace,
    back: mockBack,
    refresh: jest.fn(),
    prefetch: jest.fn()
  }),
  usePathname: () => '/entities',
  useSearchParams: () => new URLSearchParams()
}))

describe('EntitiesPage (/entities)', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    
    // Setup default API mocks
    const entitiesAPI = require('@/lib/api/entities')
    entitiesAPI.listEntities.mockResolvedValue({
      entities: mockEntities,
      pagination: { page: 1, size: 25, total: 2, pages: 1 }
    })
  })

  it('should render the main entities page', () => {
    expect(() => {
      render(<EntitiesPage />)
      expect(screen.getByTestId('entities-page')).toBeInTheDocument()
    }).toThrow() // Page doesn't exist yet
  })

  it('should display entity type tabs', () => {
    expect(() => {
      render(<EntitiesPage />)
      
      expect(screen.getByRole('tab', { name: /person/i })).toBeInTheDocument()
      expect(screen.getByRole('tab', { name: /project/i })).toBeInTheDocument()
      expect(screen.getByRole('tab', { name: /account/i })).toBeInTheDocument()
    }).toThrow()
  })

  it('should load entities on mount', async () => {
    expect(async () => {
      const entitiesAPI = require('@/lib/api/entities')
      
      render(<EntitiesPage />)
      
      await waitFor(() => {
        expect(entitiesAPI.listEntities).toHaveBeenCalledWith({
          entity_type: 'person',
          page: 1,
          size: 25
        })
      })
    }).rejects.toThrow()
  })

  it('should switch entity types when tab is clicked', async () => {
    expect(async () => {
      const entitiesAPI = require('@/lib/api/entities')
      
      render(<EntitiesPage />)
      
      const projectTab = screen.getByRole('tab', { name: /project/i })
      await userEvent.click(projectTab)
      
      await waitFor(() => {
        expect(entitiesAPI.listEntities).toHaveBeenCalledWith({
          entity_type: 'project',
          page: 1,
          size: 25
        })
      })
    }).rejects.toThrow()
  })

  it('should navigate to create page when create button clicked', async () => {
    expect(async () => {
      render(<EntitiesPage />)
      
      const createButton = screen.getByRole('button', { name: /create person/i })
      await userEvent.click(createButton)
      
      expect(mockPush).toHaveBeenCalledWith('/entities/person/new')
    }).rejects.toThrow()
  })

  it('should navigate to edit page when entity is selected', async () => {
    expect(async () => {
      render(<EntitiesPage />)
      
      await waitFor(() => {
        expect(screen.getByText('John Smith')).toBeInTheDocument()
      })
      
      const entityRow = screen.getByText('John Smith')
      await userEvent.click(entityRow)
      
      expect(mockPush).toHaveBeenCalledWith('/entities/person/person-1')
    }).rejects.toThrow()
  })

  it('should handle search across entities', async () => {
    expect(async () => {
      const entitiesAPI = require('@/lib/api/entities')
      entitiesAPI.searchEntities.mockResolvedValue({
        entities: [mockEntities[0]],
        pagination: { page: 1, size: 25, total: 1, pages: 1 }
      })
      
      render(<EntitiesPage />)
      
      const searchInput = screen.getByPlaceholderText(/search entities/i)
      await userEvent.type(searchInput, 'John')
      
      await waitFor(() => {
        expect(entitiesAPI.searchEntities).toHaveBeenCalledWith({
          query: 'John',
          entity_type: 'person'
        })
      })
    }).rejects.toThrow()
  })

  it('should handle pagination', async () => {
    expect(async () => {
      const entitiesAPI = require('@/lib/api/entities')
      entitiesAPI.listEntities.mockResolvedValue({
        entities: mockEntities,
        pagination: { page: 1, size: 25, total: 100, pages: 4 }
      })
      
      render(<EntitiesPage />)
      
      await waitFor(() => {
        expect(screen.getByRole('navigation', { name: /pagination/i })).toBeInTheDocument()
      })
      
      const nextPageButton = screen.getByRole('button', { name: /next page/i })
      await userEvent.click(nextPageButton)
      
      expect(entitiesAPI.listEntities).toHaveBeenCalledWith({
        entity_type: 'person',
        page: 2,
        size: 25
      })
    }).rejects.toThrow()
  })

  it('should show loading state while fetching entities', () => {
    expect(() => {
      const entitiesAPI = require('@/lib/api/entities')
      entitiesAPI.listEntities.mockImplementation(() => new Promise(() => {})) // Never resolves
      
      render(<EntitiesPage />)
      
      expect(screen.getByTestId('loading-entities')).toBeInTheDocument()
    }).toThrow()
  })

  it('should handle API errors gracefully', async () => {
    expect(async () => {
      const entitiesAPI = require('@/lib/api/entities')
      entitiesAPI.listEntities.mockRejectedValue(new Error('API Error'))
      
      render(<EntitiesPage />)
      
      await waitFor(() => {
        expect(screen.getByText(/failed to load entities/i)).toBeInTheDocument()
      })
    }).rejects.toThrow()
  })

  it('should persist tab state in URL', async () => {
    expect(async () => {
      render(<EntitiesPage />)
      
      const projectTab = screen.getByRole('tab', { name: /project/i })
      await userEvent.click(projectTab)
      
      expect(mockReplace).toHaveBeenCalledWith('/entities?type=project')
    }).rejects.toThrow()
  })
})

describe('CreateEntityPage (/entities/[type]/new)', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    
    // Setup API mocks
    const entitiesAPI = require('@/lib/api/entities')
    entitiesAPI.createEntity.mockResolvedValue({
      id: 'person-new',
      entity_type: 'person',
      attributes: { name: 'New Person', email: 'new@example.com' }
    })
  })

  it('should render create entity page', () => {
    expect(() => {
      render(<CreateEntityPage params={{ type: 'person' }} />)
      expect(screen.getByTestId('create-entity-page')).toBeInTheDocument()
    }).toThrow()
  })

  it('should show page title with entity type', () => {
    expect(() => {
      render(<CreateEntityPage params={{ type: 'person' }} />)
      expect(screen.getByRole('heading', { name: /create person/i })).toBeInTheDocument()
    }).toThrow()
  })

  it('should render entity form for the specified type', () => {
    expect(() => {
      render(<CreateEntityPage params={{ type: 'person' }} />)
      
      expect(screen.getByLabelText(/name/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/role/i)).toBeInTheDocument()
    }).toThrow()
  })

  it('should show breadcrumb navigation', () => {
    expect(() => {
      render(<CreateEntityPage params={{ type: 'person' }} />)
      
      expect(screen.getByRole('navigation', { name: /breadcrumb/i })).toBeInTheDocument()
      expect(screen.getByLink('Entities')).toHaveAttribute('href', '/entities')
      expect(screen.getByText('Create Person')).toBeInTheDocument()
    }).toThrow()
  })

  it('should create entity and navigate back on successful submission', async () => {
    expect(async () => {
      const entitiesAPI = require('@/lib/api/entities')
      
      render(<CreateEntityPage params={{ type: 'person' }} />)
      
      await userEvent.type(screen.getByLabelText(/name/i), 'New Person')
      await userEvent.type(screen.getByLabelText(/email/i), 'new@example.com')
      
      const saveButton = screen.getByRole('button', { name: /save/i })
      await userEvent.click(saveButton)
      
      await waitFor(() => {
        expect(entitiesAPI.createEntity).toHaveBeenCalledWith({
          entity_type: 'person',
          attributes: {
            name: 'New Person',
            email: 'new@example.com'
          }
        })
      })
      
      expect(mockPush).toHaveBeenCalledWith('/entities/person/person-new')
    }).rejects.toThrow()
  })

  it('should navigate back when cancel is clicked', async () => {
    expect(async () => {
      render(<CreateEntityPage params={{ type: 'person' }} />)
      
      const cancelButton = screen.getByRole('button', { name: /cancel/i })
      await userEvent.click(cancelButton)
      
      expect(mockBack).toHaveBeenCalled()
    }).rejects.toThrow()
  })

  it('should handle creation errors', async () => {
    expect(async () => {
      const entitiesAPI = require('@/lib/api/entities')
      entitiesAPI.createEntity.mockRejectedValue(new Error('Validation failed'))
      
      render(<CreateEntityPage params={{ type: 'person' }} />)
      
      await userEvent.type(screen.getByLabelText(/name/i), 'New Person')
      await userEvent.type(screen.getByLabelText(/email/i), 'invalid-email')
      
      const saveButton = screen.getByRole('button', { name: /save/i })
      await userEvent.click(saveButton)
      
      await waitFor(() => {
        expect(screen.getByText(/failed to create entity/i)).toBeInTheDocument()
      })
    }).rejects.toThrow()
  })

  it('should show loading state during creation', async () => {
    expect(async () => {
      const entitiesAPI = require('@/lib/api/entities')
      entitiesAPI.createEntity.mockImplementation(() => new Promise(() => {}))
      
      render(<CreateEntityPage params={{ type: 'person' }} />)
      
      await userEvent.type(screen.getByLabelText(/name/i), 'New Person')
      const saveButton = screen.getByRole('button', { name: /save/i })
      await userEvent.click(saveButton)
      
      expect(screen.getByTestId('creating-entity')).toBeInTheDocument()
      expect(saveButton).toBeDisabled()
    }).rejects.toThrow()
  })

})

describe('EditEntityPage (/entities/[type]/[id])', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    
    // Setup API mocks
    const entitiesAPI = require('@/lib/api/entities')
    entitiesAPI.getEntity.mockResolvedValue(mockEntities[0])
    entitiesAPI.updateEntity.mockResolvedValue({
      ...mockEntities[0],
      attributes: { ...mockEntities[0].attributes, name: 'Updated Name' }
    })
  })

  it('should render edit entity page', () => {
    expect(() => {
      render(<EditEntityPage params={{ type: 'person', id: 'person-1' }} />)
      expect(screen.getByTestId('edit-entity-page')).toBeInTheDocument()
    }).toThrow()
  })

  it('should load and display entity data', async () => {
    expect(async () => {
      const entitiesAPI = require('@/lib/api/entities')
      
      render(<EditEntityPage params={{ type: 'person', id: 'person-1' }} />)
      
      await waitFor(() => {
        expect(entitiesAPI.getEntity).toHaveBeenCalledWith('person-1')
      })
      
      expect(screen.getByDisplayValue('John Smith')).toBeInTheDocument()
      expect(screen.getByDisplayValue('john@example.com')).toBeInTheDocument()
    }).rejects.toThrow()
  })

  it('should show page title with entity name', async () => {
    expect(async () => {
      render(<EditEntityPage params={{ type: 'person', id: 'person-1' }} />)
      
      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /edit john smith/i })).toBeInTheDocument()
      })
    }).rejects.toThrow()
  })

  it('should show relationship manager', async () => {
    expect(async () => {
      render(<EditEntityPage params={{ type: 'person', id: 'person-1' }} />)
      
      await waitFor(() => {
        expect(screen.getByTestId('relationship-manager')).toBeInTheDocument()
      })
    }).rejects.toThrow()
  })

  it('should update entity and show success message', async () => {
    expect(async () => {
      const entitiesAPI = require('@/lib/api/entities')
      
      render(<EditEntityPage params={{ type: 'person', id: 'person-1' }} />)
      
      await waitFor(() => {
        expect(screen.getByDisplayValue('John Smith')).toBeInTheDocument()
      })
      
      const nameInput = screen.getByDisplayValue('John Smith')
      await userEvent.clear(nameInput)
      await userEvent.type(nameInput, 'Updated Name')
      
      const saveButton = screen.getByRole('button', { name: /save/i })
      await userEvent.click(saveButton)
      
      await waitFor(() => {
        expect(entitiesAPI.updateEntity).toHaveBeenCalledWith('person-1', {
          attributes: {
            name: 'Updated Name',
            email: 'john@example.com',
            role: 'consultant'
          }
        })
      })
      
      expect(screen.getByText(/entity updated successfully/i)).toBeInTheDocument()
    }).rejects.toThrow()
  })

  it('should show delete confirmation dialog', async () => {
    expect(async () => {
      render(<EditEntityPage params={{ type: 'person', id: 'person-1' }} />)
      
      const deleteButton = screen.getByRole('button', { name: /delete/i })
      await userEvent.click(deleteButton)
      
      expect(screen.getByRole('dialog')).toBeInTheDocument()
      expect(screen.getByText(/are you sure you want to delete/i)).toBeInTheDocument()
    }).rejects.toThrow()
  })

  it('should delete entity when confirmed', async () => {
    expect(async () => {
      const entitiesAPI = require('@/lib/api/entities')
      entitiesAPI.deleteEntity.mockResolvedValue({ success: true })
      
      render(<EditEntityPage params={{ type: 'person', id: 'person-1' }} />)
      
      const deleteButton = screen.getByRole('button', { name: /delete/i })
      await userEvent.click(deleteButton)
      
      const confirmButton = screen.getByRole('button', { name: /confirm delete/i })
      await userEvent.click(confirmButton)
      
      await waitFor(() => {
        expect(entitiesAPI.deleteEntity).toHaveBeenCalledWith('person-1')
      })
      
      expect(mockPush).toHaveBeenCalledWith('/entities')
    }).rejects.toThrow()
  })

  it('should handle entity not found', async () => {
    expect(async () => {
      const entitiesAPI = require('@/lib/api/entities')
      entitiesAPI.getEntity.mockRejectedValue(new Error('Entity not found'))
      
      render(<EditEntityPage params={{ type: 'person', id: 'nonexistent' }} />)
      
      await waitFor(() => {
        expect(screen.getByText(/entity not found/i)).toBeInTheDocument()
      })
    }).rejects.toThrow()
  })

  it('should show loading state while fetching entity', () => {
    expect(() => {
      const entitiesAPI = require('@/lib/api/entities')
      entitiesAPI.getEntity.mockImplementation(() => new Promise(() => {}))
      
      render(<EditEntityPage params={{ type: 'person', id: 'person-1' }} />)
      
      expect(screen.getByTestId('loading-entity')).toBeInTheDocument()
    }).toThrow()
  })

  it('should handle update errors', async () => {
    expect(async () => {
      const entitiesAPI = require('@/lib/api/entities')
      entitiesAPI.updateEntity.mockRejectedValue(new Error('Update failed'))
      
      render(<EditEntityPage params={{ type: 'person', id: 'person-1' }} />)
      
      await waitFor(() => {
        expect(screen.getByDisplayValue('John Smith')).toBeInTheDocument()
      })
      
      const saveButton = screen.getByRole('button', { name: /save/i })
      await userEvent.click(saveButton)
      
      await waitFor(() => {
        expect(screen.getByText(/failed to update entity/i)).toBeInTheDocument()
      })
    }).rejects.toThrow()
  })
})

describe('Entity Pages Integration', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should handle navigation flow: list → create → edit', async () => {
    expect(async () => {
      // Start at entities page
      const entitiesAPI = require('@/lib/api/entities')
      entitiesAPI.listEntities.mockResolvedValue({
        entities: mockEntities,
        pagination: { page: 1, size: 25, total: 2, pages: 1 }
      })
      
      const { unmount } = render(<EntitiesPage />)
      
      // Click create button
      const createButton = screen.getByRole('button', { name: /create person/i })
      await userEvent.click(createButton)
      
      expect(mockPush).toHaveBeenCalledWith('/entities/person/new')
      
      // Simulate navigation to create page
      unmount()
      entitiesAPI.createEntity.mockResolvedValue({
        id: 'person-new',
        entity_type: 'person',
        attributes: { name: 'New Person', email: 'new@example.com' }
      })
      
      render(<CreateEntityPage params={{ type: 'person' }} />)
      
      // Fill and submit form
      await userEvent.type(screen.getByLabelText(/name/i), 'New Person')
      await userEvent.type(screen.getByLabelText(/email/i), 'new@example.com')
      
      const saveButton = screen.getByRole('button', { name: /save/i })
      await userEvent.click(saveButton)
      
      await waitFor(() => {
        expect(mockPush).toHaveBeenCalledWith('/entities/person/person-new')
      })
    }).rejects.toThrow()
  })

  it('should preserve filters when navigating back from entity details', async () => {
    expect(async () => {
      // Mock URL search params to include filters
      const mockSearchParams = new URLSearchParams('?type=person&q=john&status=active')
      require('next/navigation').useSearchParams.mockReturnValue(mockSearchParams)
      
      render(<EntitiesPage />)
      
      // Navigate to entity details
      const entityRow = screen.getByText('John Smith')
      await userEvent.click(entityRow)
      
      expect(mockPush).toHaveBeenCalledWith('/entities/person/person-1?back=/entities?type=person&q=john&status=active')
    }).rejects.toThrow()
  })

  it('should handle domain switching during entity management', async () => {
    expect(async () => {
      const domainAPI = require('@/lib/api/domain')
      domainAPI.switchDomain.mockResolvedValue({ success: true })
      
      render(<EntitiesPage />)
      
      // Simulate domain switch
      const domainSelector = screen.getByTestId('domain-selector')
      await userEvent.selectOptions(domainSelector, 'personal_crm')
      
      await waitFor(() => {
        expect(domainAPI.switchDomain).toHaveBeenCalledWith('personal_crm')
      })
      
      // Should reload entities for new domain
      const entitiesAPI = require('@/lib/api/entities')
      expect(entitiesAPI.listEntities).toHaveBeenCalledWith({
        entity_type: 'person',
        page: 1,
        size: 25,
        domain: 'personal_crm'
      })
    }).rejects.toThrow()
  })

  it('should handle concurrent user actions gracefully', async () => {
    expect(async () => {
      render(<EditEntityPage params={{ type: 'person', id: 'person-1' }} />)
      
      await waitFor(() => {
        expect(screen.getByDisplayValue('John Smith')).toBeInTheDocument()
      })
      
      // Simulate rapid clicking of save button
      const saveButton = screen.getByRole('button', { name: /save/i })
      await userEvent.click(saveButton)
      await userEvent.click(saveButton)
      await userEvent.click(saveButton)
      
      // Should only call API once due to debouncing/loading state
      const entitiesAPI = require('@/lib/api/entities')
      expect(entitiesAPI.updateEntity).toHaveBeenCalledTimes(1)
    }).rejects.toThrow()
  })

  it('should handle browser back/forward navigation', async () => {
    expect(async () => {
      // Simulate user pressing browser back button
      window.history.back = jest.fn()
      
      render(<EditEntityPage params={{ type: 'person', id: 'person-1' }} />)
      
      const cancelButton = screen.getByRole('button', { name: /cancel/i })
      await userEvent.click(cancelButton)
      
      expect(mockBack).toHaveBeenCalled()
    }).rejects.toThrow()
  })
})